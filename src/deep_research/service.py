# src/deep_research/service.py
"""M_20 DeepResearchService — 계획→반복 검색→격차 분석→종합 파이프라인 (스펙 §3).

인터넷 검색 없이 사내 지식 기반(GraphRAG 하이브리드 + 벡터 RAG)만 사용한다.
그래프 저장소 미가용 시 벡터-only 폴백 (기능 저하, 오류 아님 — M_19와 동일 원칙).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from vector_search.types import SearchHit

from .prompts import (
    NO_EVIDENCE_REPORT,
    PLANNER_JSON_SCHEMA,
    gap_prompts,
    planner_prompts,
    synthesis_prompts,
)

logger = logging.getLogger(__name__)

RESEARCH_MODES = ("duplication", "discovery", "proposal")

# 근거 예산 (스펙 §3)
_TOP_K_PER_QUERY = 5
_MAX_EVIDENCE_CHUNKS = 24
_MAX_EVIDENCE_CHARS = 14_000
_MAX_SUB_QUERIES = 6
_MAX_GAP_QUERIES = 3
_MAX_INPUT_CHARS = 30_000

_PLANNER_TIMEOUT = 90.0
_GAP_TIMEOUT = 90.0
_SYNTHESIS_TIMEOUT = 600.0
_SYNTHESIS_MAX_TOKENS = 4096


class _CompletionAgent(Protocol):
    """GemmaChatAgent의 비스트리밍 완성 프로토콜 (회의록 generator와 동형)."""

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        *,
        max_tokens: int = ...,
        temperature: float = ...,
        timeout_seconds: float = ...,
    ) -> dict[str, Any]: ...

    async def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = ...,
        temperature: float = ...,
        timeout_seconds: float = ...,
    ) -> str: ...


class DeepResearchService:
    """딥 리서치 오케스트레이터 — 동시 실행 1개 제한."""

    def __init__(
        self,
        agent: _CompletionAgent,
        rag_service: Any,  # vector_search.RagService (순환 의존 회피용 Any)
        graph_rag_service: Any = None,  # GraphRagService | None
        prompt_provider: "Callable[[str], str] | None" = None,
    ) -> None:
        self._agent = agent
        self._rag = rag_service
        self._graph_rag = graph_rag_service
        self._lock = asyncio.Lock()
        # M_17 연동 (CR-44): mode("duplication"/"discovery"/"proposal") → 커스텀 지침
        # raw 텍스트(빈 문자열 = 미설정). 호출 시점 lazy 조회라 지침 저장 즉시 다음
        # 실행부터 반영되고, agent 재초기화가 필요 없다.
        self._prompt_provider = prompt_provider

    # ── 파이프라인 ────────────────────────────────────────────────────────────

    async def run(
        self,
        mode: str,
        prompt: str,
        attachment_text: str = "",
        scope_doc_ids: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """딥 리서치 실행. 도는 동안 배경 인제스트를 쉬게 한다 (CR-54).

        리서치는 128B 모델을 GPU에 올린 채 수 분간 돈다. 그 사이 임베딩이 계속 돌면
        GPU가 고갈돼 백엔드가 죽은 적이 있다 (E-87).
        """
        from rag_watch.activity import conversation_active

        with conversation_active():
            async for _ev in self._run_inner(mode, prompt, attachment_text, scope_doc_ids):
                yield _ev

    async def _run_inner(
        self,
        mode: str,
        prompt: str,
        attachment_text: str = "",
        scope_doc_ids: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """딥 리서치 실행 — 진행 이벤트를 순차 yield (스펙 §3).

        scope_doc_ids가 주어지면 검색 근거를 해당 문서들로 제한한다
        (CR-21 연동: 그래프 탭에서 핀 꽂은 문서 범위 리서치).
        마지막 이벤트는 {stage:"done", report, sources, sub_queries} 또는
        {stage:"error", message}.
        """
        if mode not in RESEARCH_MODES:
            yield {"stage": "error", "message": f"알 수 없는 모드: {mode!r}"}
            return

        user_input = self._build_user_input(prompt, attachment_text)
        if not user_input.strip():
            yield {"stage": "error", "message": "요청 내용이 비어 있습니다."}
            return
        if len(user_input) > _MAX_INPUT_CHARS:
            user_input = user_input[:_MAX_INPUT_CHARS]
            yield {
                "stage": "notice",
                "message": f"입력이 길어 앞 {_MAX_INPUT_CHARS:,}자만 사용합니다.",
            }

        if self._lock.locked():
            yield {
                "stage": "error",
                "message": "이미 딥 리서치가 진행 중입니다. 완료 후 다시 시도해 주세요.",
            }
            return

        scope = {s for s in (scope_doc_ids or []) if s}
        if scope:
            yield {
                "stage": "notice",
                "message": f"검색 범위를 지정 문서 {len(scope)}건으로 제한합니다.",
            }

        async with self._lock:
            async for event in self._run_pipeline(mode, user_input, scope or None):
                yield event

    async def _run_pipeline(
        self, mode: str, user_input: str, scope: set[str] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        # 1. 계획
        yield {"stage": "planning", "message": "검색 계획 수립 중..."}
        sub_queries = await self._plan(mode, user_input)
        yield {
            "stage": "planned",
            "message": f"하위 질의 {len(sub_queries)}개 생성",
            "sub_queries": sub_queries,
        }

        # 2. 검색 (그래프 미가용 시 벡터-only 고지)
        if self._graph_rag is None or not getattr(self._graph_rag, "available", False):
            yield {
                "stage": "notice",
                "message": "지식그래프 미연결 — 벡터 검색만 사용합니다.",
            }

        pool: dict[str, SearchHit] = {}  # chunk_id → hit (중복 제거)
        for i, q in enumerate(sub_queries, 1):
            yield {"stage": "searching", "message": f"자료 검색 {i}/{len(sub_queries)}: {q}"}
            hits = await self._retrieve(q, scope)
            new = self._merge_hits(pool, hits)
            yield {
                "stage": "searched",
                "message": f"'{q}' → {len(hits)}건 (신규 {new}건, 누적 {len(pool)}건)",
            }

        # 3. 격차 분석 (1라운드, 실패 시 스킵)
        gap_queries: list[str] = []
        if pool:
            yield {"stage": "gap_analysis", "message": "수집 근거 검토·보완 질의 생성 중..."}
            gap_queries = await self._gap_queries(mode, user_input, pool)
            for i, q in enumerate(gap_queries, 1):
                yield {"stage": "searching", "message": f"보완 검색 {i}/{len(gap_queries)}: {q}"}
                hits = await self._retrieve(q, scope)
                new = self._merge_hits(pool, hits)
                yield {
                    "stage": "searched",
                    "message": f"'{q}' → {len(hits)}건 (신규 {new}건, 누적 {len(pool)}건)",
                }

        all_queries = sub_queries + gap_queries

        # 4. 종합
        sources = self._rank_sources(pool)
        if not sources:
            logger.info("DeepResearch: 근거 0건 — 보고서 생성 생략 (mode=%s)", mode)
            yield {
                "stage": "done",
                "report": NO_EVIDENCE_REPORT,
                "sources": [],
                "sub_queries": all_queries,
            }
            return

        yield {
            "stage": "synthesis",
            "message": f"근거 {len(sources)}건으로 보고서 작성 중... (수 분 소요될 수 있음)",
        }
        evidence_block = self._evidence_block(sources)
        custom_instructions = ""
        if self._prompt_provider is not None:
            try:
                custom_instructions = (self._prompt_provider(mode) or "").strip()
            except Exception as exc:
                logger.warning("DeepResearch 커스텀 지침 조회 실패 (기본값 사용): %s", exc)
        system, user = synthesis_prompts(mode, user_input, evidence_block, custom_instructions)
        try:
            report = await self._agent.complete_text(
                system,
                user,
                max_tokens=_SYNTHESIS_MAX_TOKENS,
                temperature=0.3,
                timeout_seconds=_SYNTHESIS_TIMEOUT,
            )
        except Exception as exc:
            logger.error("DeepResearch 종합 실패: %s", exc)
            yield {"stage": "error", "message": f"보고서 생성 실패: {exc}"}
            return

        logger.info(
            "DeepResearch 완료: mode=%s, 질의=%d, 근거=%d청크, 보고서=%d자",
            mode,
            len(all_queries),
            len(sources),
            len(report),
        )
        yield {
            "stage": "done",
            "report": report,
            "sources": [self._source_dict(n, h) for n, h in enumerate(sources, 1)],
            "sub_queries": all_queries,
        }

    # ── 단계 구현 ─────────────────────────────────────────────────────────────

    async def _plan(self, mode: str, user_input: str) -> list[str]:
        """하위 질의 생성. 실패 시 사용자 입력 앞부분을 단일 질의로 폴백."""
        system, user = planner_prompts(mode, user_input)
        try:
            raw = await self._agent.complete_json(
                system,
                user,
                PLANNER_JSON_SCHEMA,
                max_tokens=1024,
                temperature=0.3,
                timeout_seconds=_PLANNER_TIMEOUT,
            )
            queries = _clean_queries(raw.get("sub_queries"))
            if queries:
                return queries[:_MAX_SUB_QUERIES]
        except Exception as exc:
            logger.warning("DeepResearch 계획 실패 (단일 질의 폴백): %s", exc)
        return [user_input[:200].strip()]

    async def _retrieve(self, query: str, scope: set[str] | None = None) -> list[SearchHit]:
        """하이브리드 검색 (그래프 미가용 시 벡터-only). 실패 시 빈 목록.

        scope가 있으면 넉넉히 검색한 뒤 doc_id로 사후 필터링해 top_k를 채운다.
        """
        top_k = _TOP_K_PER_QUERY * 4 if scope else _TOP_K_PER_QUERY
        try:
            if self._graph_rag is not None:
                result = await self._graph_rag.hybrid_retrieve(query, top_k=top_k)
                hits = list(result.hits)
            else:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, lambda: self._rag.retrieve(query, top_k))
                hits = list(result.hits)
        except Exception as exc:
            logger.warning("DeepResearch 검색 실패 (query=%r): %s", query[:50], exc)
            return []
        if scope:
            hits = [h for h in hits if h.doc_id in scope]
        return hits[:_TOP_K_PER_QUERY]

    async def _gap_queries(
        self, mode: str, user_input: str, pool: dict[str, SearchHit]
    ) -> list[str]:
        """수집 근거를 보여주고 보완 질의 생성. 실패·불필요 시 빈 목록."""
        digest = "\n".join(f"- {h.doc_name}: {h.text[:80]}" for h in list(pool.values())[:20])
        system, user = gap_prompts(mode, user_input, digest)
        try:
            raw = await self._agent.complete_json(
                system,
                user,
                PLANNER_JSON_SCHEMA,
                max_tokens=512,
                temperature=0.3,
                timeout_seconds=_GAP_TIMEOUT,
            )
            return _clean_queries(raw.get("sub_queries"))[:_MAX_GAP_QUERIES]
        except Exception as exc:
            logger.warning("DeepResearch 격차 분석 실패 (스킵): %s", exc)
            return []

    # ── 근거 조립 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _merge_hits(pool: dict[str, SearchHit], hits: list[SearchHit]) -> int:
        """chunk_id 기준 중복 제거 병합. 반환: 신규 추가 수."""
        added = 0
        for h in hits:
            key = h.chunk_id or f"{h.doc_id}:{hash(h.text)}"
            if key not in pool:
                pool[key] = h
                added += 1
            elif h.score > pool[key].score:
                pool[key] = h
        return added

    @staticmethod
    def _rank_sources(pool: dict[str, SearchHit]) -> list[SearchHit]:
        """문서(doc_id)별 최고 score 청크 1개로 중복 제거 → score 내림차순 + 상한.

        같은 문서의 여러 청크가 각각 참고자료 번호를 차지해 중복 표시되던 문제 방지
        (레퍼런스·인용 번호를 문서 단위로 정렬)."""
        best: dict[str, SearchHit] = {}
        for h in pool.values():
            cur = best.get(h.doc_id)
            if cur is None or h.score > cur.score:
                best[h.doc_id] = h
        ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)
        return ranked[:_MAX_EVIDENCE_CHUNKS]

    @staticmethod
    def _evidence_block(sources: list[SearchHit]) -> str:
        """[n] 번호 목록 텍스트 — 총량 상한 초과 시 뒤(낮은 score)부터 절단."""
        lines: list[str] = []
        total = 0
        for n, h in enumerate(sources, 1):
            page = f" p.{h.page}" if h.page else ""
            entry = f"[{n}] {h.doc_name}{page}\n{h.text.strip()}\n"
            if total + len(entry) > _MAX_EVIDENCE_CHARS:
                break
            lines.append(entry)
            total += len(entry)
        return "\n".join(lines)

    @staticmethod
    def _source_dict(n: int, h: SearchHit) -> dict[str, Any]:
        return {
            "n": n,
            "doc_id": h.doc_id,
            "doc_name": h.doc_name,
            "page": h.page,
            "score": round(float(h.score), 3),
        }

    @staticmethod
    def _build_user_input(prompt: str, attachment_text: str) -> str:
        parts = [prompt.strip()]
        if attachment_text.strip():
            parts.append(f"## 첨부 자료 내용\n{attachment_text.strip()}")
        return "\n\n".join(p for p in parts if p)


def _clean_queries(raw: Any) -> list[str]:
    """LLM 출력의 sub_queries를 견고하게 정제 (E-51식: 형식 변형 허용)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        q = (
            str(item).strip()
            if not isinstance(item, dict)
            else str(item.get("query") or item.get("q") or "").strip()
        )
        if q and len(q) >= 2 and q not in seen:
            seen.add(q)
            out.append(q[:100])
    return out
