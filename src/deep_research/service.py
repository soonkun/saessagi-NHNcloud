# src/deep_research/service.py
"""M_20 DeepResearchService — 계획→반복 검색→격차 분석→종합 파이프라인 (스펙 §3).

인터넷 검색 없이 사내 지식 기반(GraphRAG 하이브리드 + 벡터 RAG)만 사용한다.
그래프 저장소 미가용 시 벡터-only 폴백 (기능 저하, 오류 아님 — M_19와 동일 원칙).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
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

# CR-62: 모드 상수를 없앴다. 무엇을 어떤 관점으로 쓰는지는 방(프로젝트)의 지침이 정하고,
# 검색 예산도 방마다 다르다. 서비스는 방의 존재를 모른 채 아래 프로필만 받는다.


@dataclass(frozen=True)
class ResearchProfile:
    """한 번의 실행에 필요한 모든 것. 방에서 뽑아 넘긴다.

    이 dataclass가 있어서 서비스가 저장소·모드·레지스트리를 전혀 몰라도 된다.
    테스트에서도 방을 만들 필요 없이 프로필만 지어 넣으면 된다.
    """

    project_id: str = "adhoc"
    name: str = ""
    instructions: str = ""
    planner_hint: str = ""
    sub_queries: int = 6
    top_k_per_query: int = 5
    gap_rounds: int = 1
    max_evidence_chunks: int = 24


# 근거 소스 — **문서만. 업무 노트는 제외한다** (E-96).
#
# 딥 리서치 보고서는 "업무노트로 저장"으로 노트가 되고, 그 노트는 벡터 스토어에 들어간다.
# 노트를 검색하면 자기 출력이 자기 근거가 되는 순환이 생긴다 — LLM이 지어낸 문장이 다음
# 보고서에서 `[3]`으로 인용된 사실이 된다. 실측으로 노트 4건이 전부 딥 리서치 산출물이었다.
_EVIDENCE_SOURCE = "docs"

# 노트를 가려내는 카테고리 마커 (`knowledge.service.KNOWLEDGE_CATEGORY`와 같은 값).
# 여기서 그 모듈을 import하지 않는 이유는 딥 리서치가 지식노트 모듈에 의존할 이유가
# 없어서다 — 값이 바뀌면 이 상수도 같이 고친다.
_NOTE_CATEGORY = "__knowledge__"


def _is_note(hit: SearchHit) -> bool:
    """업무 노트에서 온 근거인가."""
    return (hit.category or "") == _NOTE_CATEGORY or str(hit.doc_id or "").startswith(
        f"{_NOTE_CATEGORY}:"
    )


# 근거 예산 기본값 (스펙 §3). 방 설정이 없을 때의 폴백이자 상한 계산의 기준.
_TOP_K_PER_QUERY = 5
_MAX_EVIDENCE_CHUNKS = 24
_MAX_EVIDENCE_CHARS = 14_000
_MAX_SUB_QUERIES = 6
_MAX_GAP_QUERIES = 3
_MAX_INPUT_CHARS = 30_000

_PLANNER_TIMEOUT = 90.0
_GAP_TIMEOUT = 90.0
_SYNTHESIS_TIMEOUT = 600.0
# 보고서 생성 중 "아직 살아 있다"를 알리는 간격. 너무 잦으면 로그·네트워크만 시끄럽고,
# 너무 뜸하면 화면이 멈춘 것처럼 보인다.
_SYNTHESIS_TICK_SEC = 10.0
# 대기 중 "아직 줄 서 있다"를 알리는 간격.
_QUEUE_TICK_SEC = 5.0
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
        max_queue_size: int = 5,
        max_queue_wait_seconds: float = 900.0,
    ) -> None:
        self._agent = agent
        self._rag = rag_service
        self._graph_rag = graph_rag_service
        self._lock = asyncio.Lock()
        # 대기열 깊이. 잠금 자체는 순서를 보장하지 않지만, 몇 명이 기다리는지는
        # 사용자에게 알려 줘야 한다.
        self._waiting = 0
        self._max_queue_size = max_queue_size
        self._max_queue_wait = max_queue_wait_seconds
        # CR-62: prompt_provider(M_17 mode→지침 조회)를 제거했다. 지침은 방에서 오고
        # 호출자가 ResearchProfile에 담아 넘긴다 — 서비스가 레지스트리를 알 필요가 없다.

    # ── 파이프라인 ────────────────────────────────────────────────────────────

    async def run(
        self,
        profile: ResearchProfile,
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
            async for _ev in self._run_inner(profile, prompt, attachment_text, scope_doc_ids):
                yield _ev

    async def _run_inner(
        self,
        profile: ResearchProfile,
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

        scope = {s for s in (scope_doc_ids or []) if s}
        if scope:
            yield {
                "stage": "notice",
                "message": f"검색 범위를 지정 문서 {len(scope)}건으로 제한합니다.",
            }

        # 동시 실행은 하나로 묶되, 겹친 요청은 **거절하지 않고 줄을 세운다** (CR-61).
        # 여러 사람이 같이 쓰는 상황에서 "이미 진행 중"만 뱉으면 언제 다시 눌러야 할지
        # 알 수 없고, 그냥 실패로 보인다.
        if self._lock.locked():
            if self._max_queue_size <= 0:
                yield {
                    "stage": "error",
                    "message": "이미 딥 리서치가 진행 중입니다. 완료 후 다시 시도해 주세요.",
                }
                return
            if self._waiting >= self._max_queue_size:
                yield {
                    "stage": "error",
                    "message": (
                        f"대기열이 가득 찼습니다(최대 {self._max_queue_size}건). "
                        "잠시 후 다시 시도해 주세요."
                    ),
                }
                return

        acquired = False
        self._waiting += 1
        position = self._waiting
        try:
            if self._lock.locked():
                yield {
                    "stage": "queued",
                    "position": position,
                    "message": (
                        f"다른 리서치가 진행 중입니다. 대기 {position}번째 — "
                        "차례가 되면 자동으로 시작합니다."
                    ),
                }
            waited = 0.0
            while True:
                try:
                    await asyncio.wait_for(self._lock.acquire(), timeout=_QUEUE_TICK_SEC)
                    acquired = True
                    break
                except (TimeoutError, asyncio.TimeoutError):
                    waited += _QUEUE_TICK_SEC
                    if waited >= self._max_queue_wait:
                        yield {
                            "stage": "error",
                            "message": (
                                f"앞선 리서치가 {int(waited / 60)}분 넘게 끝나지 않아 "
                                "기다리기를 멈췄습니다. 잠시 후 다시 시도해 주세요."
                            ),
                        }
                        return
                    # 살아 있다는 신호 — 없으면 화면이 멈춘 것처럼 보인다.
                    yield {
                        "stage": "queued",
                        "position": position,
                        "elapsed_seconds": int(waited),
                        "message": f"대기 중… {int(waited)}초 경과 (대기 {position}번째)",
                    }
        finally:
            self._waiting -= 1

        try:
            # 줄을 섰던 경우에만 알린다. 바로 시작한 요청에까지 안내를 붙이면
            # 기존 이벤트 순서(planning이 첫 이벤트)가 깨진다.
            if position > 1:
                yield {"stage": "notice", "message": "차례가 되어 시작합니다."}
            async for event in self._run_pipeline(profile, user_input, scope or None):
                yield event
        finally:
            if acquired:
                self._lock.release()

    async def _run_pipeline(
        self, profile: ResearchProfile, user_input: str, scope: set[str] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        # 1. 계획
        yield {"stage": "planning", "message": "검색 계획 수립 중..."}
        sub_queries = await self._plan(profile, user_input)
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
            hits = await self._retrieve(q, scope, profile.top_k_per_query)
            new = self._merge_hits(pool, hits)
            yield {
                "stage": "searched",
                "message": f"'{q}' → {len(hits)}건 (신규 {new}건, 누적 {len(pool)}건)",
            }

        # 3. 격차 분석 — 방 설정만큼 반복 (CR-62. 예전에는 1회가 코드에 펼쳐져 있었다).
        #    새 근거가 하나도 안 늘면 더 돌 이유가 없으므로 조기 종료한다.
        gap_queries: list[str] = []
        for round_no in range(1, max(0, profile.gap_rounds) + 1):
            if not pool:
                break
            label = f" ({round_no}/{profile.gap_rounds})" if profile.gap_rounds > 1 else ""
            yield {
                "stage": "gap_analysis",
                "message": f"수집 근거 검토·보완 질의 생성 중...{label}",
            }
            queries = await self._gap_queries(profile, user_input, pool)
            if not queries:
                break
            gap_queries.extend(queries)
            added = 0
            for i, q in enumerate(queries, 1):
                yield {"stage": "searching", "message": f"보완 검색{label} {i}/{len(queries)}: {q}"}
                hits = await self._retrieve(q, scope, profile.top_k_per_query)
                new = self._merge_hits(pool, hits)
                added += new
                yield {
                    "stage": "searched",
                    "message": f"'{q}' → {len(hits)}건 (신규 {new}건, 누적 {len(pool)}건)",
                }
            if added == 0:
                break

        all_queries = sub_queries + gap_queries

        # 4. 종합
        sources = self._rank_sources(pool, profile.max_evidence_chunks)
        if not sources:
            logger.info("DeepResearch: 근거 0건 — 보고서 생성 생략 (방=%s)", profile.project_id)
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
        system, user = synthesis_prompts(profile.instructions, user_input, evidence_block)
        # 보고서 생성은 수 분짜리 단일 호출이라, 그냥 await하면 그동안 스트림에 아무것도
        # 흐르지 않는다. 화면에서는 진행 중인지 서버가 죽은 것인지 구분할 수 없다
        # (사용자 지적). 살아 있다는 신호를 주기적으로 보낸다 (CR-57).
        started = time.monotonic()
        task = asyncio.create_task(
            self._agent.complete_text(
                system,
                user,
                max_tokens=_SYNTHESIS_MAX_TOKENS,
                temperature=0.3,
                timeout_seconds=_SYNTHESIS_TIMEOUT,
            )
        )
        try:
            while True:
                finished, _ = await asyncio.wait({task}, timeout=_SYNTHESIS_TICK_SEC)
                if finished:
                    break
                yield {
                    "stage": "synthesis_tick",
                    "elapsed_seconds": int(time.monotonic() - started),
                }
            report = task.result()
        except Exception as exc:
            task.cancel()
            logger.error("DeepResearch 종합 실패: %s", exc)
            yield {"stage": "error", "message": f"보고서 생성 실패: {exc}"}
            return

        logger.info(
            "DeepResearch 완료: 방=%s, 질의=%d, 근거=%d청크, 보고서=%d자",
            profile.project_id,
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

    async def _plan(self, profile: ResearchProfile, user_input: str) -> list[str]:
        """하위 질의 생성. 실패 시 사용자 입력 앞부분을 단일 질의로 폴백."""
        system, user = planner_prompts(profile.planner_hint, user_input)
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
                return queries[: max(1, profile.sub_queries)]
        except Exception as exc:
            logger.warning("DeepResearch 계획 실패 (단일 질의 폴백): %s", exc)
        return [user_input[:200].strip()]

    async def _retrieve(
        self, query: str, scope: set[str] | None = None, top_k_per_query: int = _TOP_K_PER_QUERY
    ) -> list[SearchHit]:
        """하이브리드 검색 (그래프 미가용 시 벡터-only). 실패 시 빈 목록.

        scope가 있으면 넉넉히 검색한 뒤 doc_id로 사후 필터링해 top_k를 채운다.

        **업무 노트는 근거로 쓰지 않는다** (`source="docs"`, E-96).
        딥 리서치 보고서는 "업무노트로 저장" 버튼으로 노트가 되고, 그 노트가 다시
        벡터 스토어에 들어간다. 노트를 검색하면 **자기 출력이 자기 근거가 되는 순환**이
        생긴다 — LLM이 지어낸 문장이 다음 보고서에서 인용된 사실로 승격된다.
        실측으로 벡터 스토어의 노트 4건이 전부 딥 리서치 산출물이었다
        (`__knowledge__:중복성검토…`).

        사내 문서만 근거로 삼는다는 것이 이 기능의 전제(스펙 §1)이기도 하다.
        """
        top_k = top_k_per_query * 4 if scope else top_k_per_query
        try:
            if self._graph_rag is not None:
                result = await self._graph_rag.hybrid_retrieve(
                    query, top_k=top_k, source=_EVIDENCE_SOURCE
                )
                hits = list(result.hits)
            else:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: self._rag.retrieve(query, top_k, source=_EVIDENCE_SOURCE)
                )
                hits = list(result.hits)
        except Exception as exc:
            logger.warning("DeepResearch 검색 실패 (query=%r): %s", query[:50], exc)
            return []
        if scope:
            hits = [h for h in hits if h.doc_id in scope]
        # 그래프 경로가 노트를 섞어 올 여지를 마지막으로 한 번 더 막는다.
        # 지금 지식그래프에는 노트가 0건이지만, 나중에 노트를 넣게 되면 이 한 줄이
        # 없을 때 순환이 조용히 되살아난다.
        hits = [h for h in hits if not _is_note(h)]
        return hits[:top_k_per_query]

    async def _gap_queries(
        self, profile: ResearchProfile, user_input: str, pool: dict[str, SearchHit]
    ) -> list[str]:
        """수집 근거를 보여주고 보완 질의 생성. 실패·불필요 시 빈 목록."""
        digest = "\n".join(f"- {h.doc_name}: {h.text[:80]}" for h in list(pool.values())[:20])
        system, user = gap_prompts(profile.name, user_input, digest)
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
    def _rank_sources(
        pool: dict[str, SearchHit], max_chunks: int = _MAX_EVIDENCE_CHUNKS
    ) -> list[SearchHit]:
        """문서(doc_id)별 최고 score 청크 1개로 중복 제거 → score 내림차순 + 상한.

        같은 문서의 여러 청크가 각각 참고자료 번호를 차지해 중복 표시되던 문제 방지
        (레퍼런스·인용 번호를 문서 단위로 정렬)."""
        best: dict[str, SearchHit] = {}
        for h in pool.values():
            cur = best.get(h.doc_id)
            if cur is None or h.score > cur.score:
                best[h.doc_id] = h
        ranked = sorted(best.values(), key=lambda h: h.score, reverse=True)
        return ranked[: max(1, max_chunks)]

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
