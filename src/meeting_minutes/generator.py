# src/meeting_minutes/generator.py
"""M_13 MeetingMinutes — LLM 호출 및 MeetingDraft 생성기."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .errors import MeetingDraftError, MeetingDraftValidationError
from .prompts import (
    CHUNK_BULLETS_SCHEMA,
    CHUNK_SUMMARY_SYSTEM_PROMPT,
    CHUNK_SUMMARY_USER_TEMPLATE,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    VOLUME_GUIDE_1PAGE,
    VOLUME_GUIDE_2PAGE,
)
from .schemas import MEETING_DRAFT_SCHEMA
from .types import DetailItem, MeetingDraft, NextStepItem, PageCount, SubItem, SummaryItem

ProgressCallback = Callable[[str, str], Awaitable[None]]

# 이 글자 수 이하면 LLM에 직접 전달. 초과하면 청크 분할 → 요약 → 합산.
# 기준: Ollama 런타임 컨텍스트 32K 토큰(gemma4 8B, RTX 4090) — 한국어 1자≈1토큰
# 최악 가정으로 20,000자 + 시스템 프롬프트 + 출력 4K 토큰이 안전하게 수용된다.
# 분할 요약은 구간당 글머리 몇 줄로 압축돼 수치·맥락 손실이 크므로 가급적 직접 전달.
_TRANSCRIPT_DIRECT_MAX_CHARS = 20_000
# 청크 한 개의 최대 글자 수 (직접 전달 한도 초과 시에만 사용)
_CHUNK_SIZE_CHARS = 8_000

logger = logging.getLogger(__name__)


class _ChatAgentLike(Protocol):
    """M_05 GemmaChatAgent가 만족해야 하는 최소 인터페이스 (CR-MM-A)."""

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        timeout_seconds: float = 60.0,
    ) -> dict[str, Any]: ...

    async def complete_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        timeout_seconds: float = 120.0,
    ) -> str: ...


def _check_length_violations(draft_dict: dict[str, Any], pages: PageCount) -> list[str]:
    """위반 사항을 문자열 리스트로 반환. 빈 리스트면 통과.

    스펙 §7.4 기준:
    - ○ 텍스트 > 73자
    - - 텍스트 > 73자
    - * 텍스트 > 86자
    - 1장에서 본문 줄 수 > 14
    - 2장에서 본문 줄 수 > 28
    """
    violations: list[str] = []
    all_items: list[dict[str, Any]] = list(draft_dict.get("summary_items", [])) + list(
        draft_dict.get("detail_items", [])
    )
    for item in all_items:
        if len(item["text"]) > 73:
            violations.append(f"○ '{item['text'][:20]}...' 길이 {len(item['text'])} > 73")
        for sub in item.get("subs", []):
            if len(sub["text"]) > 73:
                violations.append(f"- '{sub['text'][:20]}...' 길이 {len(sub['text'])} > 73")
            if sub.get("detail") and len(sub["detail"]) > 86:
                violations.append(f"* '{sub['detail'][:20]}...' 길이 {len(sub['detail'])} > 86")

    total_lines = sum(
        1 + len(it.get("subs", [])) + sum(1 for s in it.get("subs", []) if s.get("detail"))
        for it in all_items
    )
    if pages == 1 and total_lines > 14:
        violations.append(f"1장 분량 초과: 본문 {total_lines}줄 > 14")
    if pages == 2 and total_lines > 28:
        violations.append(f"2장 분량 초과: 본문 {total_lines}줄 > 28")
    return violations


def _normalize_raw_draft(raw: dict[str, Any]) -> dict[str, Any]:
    """LLM 응답의 흔한 형식 일탈을 스키마 검증 전에 정규화한다 (E-41).

    - 빈 text + detail만 있는 sub: 직전 sub의 추가 *로 취급 — 직전 sub의 text를
      복제해 살린다 (HwpxWriter가 연속 동일 - 텍스트를 병합).
    - text·detail 모두 빈 sub, text 빈 item: 제거.
    """
    if not isinstance(raw, dict):
        return raw

    for key in ("summary_items", "detail_items"):
        items = raw.get(key)
        if not isinstance(items, list):
            continue
        cleaned_items: list[Any] = []
        for item in items:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                continue
            subs = item.get("subs")
            if isinstance(subs, list):
                cleaned_subs: list[Any] = []
                for sub in subs:
                    if not isinstance(sub, dict):
                        continue
                    text = str(sub.get("text", "")).strip()
                    detail = str(sub.get("detail", "") or "").strip()
                    if not text and detail and cleaned_subs:
                        sub = {**sub, "text": cleaned_subs[-1]["text"]}
                    elif not text:
                        continue
                    cleaned_subs.append(sub)
                # 스키마 한도(4) 초과 subs는 잘라낸다 — 재시도 후 하드 실패보다
                # 일부 부연 생략이 낫다 (E-43)
                if len(cleaned_subs) > 4:
                    logger.warning(
                        f"subs {len(cleaned_subs)}개 → 4개로 절단 (item={item.get('text', '')[:20]!r})"
                    )
                    cleaned_subs = cleaned_subs[:4]
                item = {**item, "subs": cleaned_subs}
            cleaned_items.append(item)
        raw[key] = cleaned_items

    next_steps = raw.get("next_steps")
    if isinstance(next_steps, list):
        raw["next_steps"] = [
            s for s in next_steps if isinstance(s, dict) and str(s.get("text", "")).strip()
        ]

    # 주요내용(detail_items) 비움 방지 — LLM이 모든 ○를 summary_items(개요)에
    # 몰아넣으면 보고서에서 '주요내용' 카테고리 헤더가 통째로 사라진다.
    # 첫 항목(회의 목적)만 개요에 남기고 나머지를 주요내용으로 이동한다.
    summary_list = raw.get("summary_items")
    detail_list = raw.get("detail_items")
    if (
        isinstance(summary_list, list)
        and isinstance(detail_list, list)
        and not detail_list
        and len(summary_list) > 1
    ):
        raw["detail_items"] = summary_list[1:]
        raw["summary_items"] = summary_list[:1]

    return raw


def _strip_marker(text: str) -> str:
    """LLM이 규칙을 어기고 text 앞에 붙인 위계 기호(○/-/* 등)를 제거한다.

    HwpxWriter가 위계 기호를 직접 붙이므로 여기 남아 있으면 '○ ○'처럼 중복된다.
    """
    return re.sub(r"^[\s○◦•·\-–—*]+", "", text).strip()


def _dict_to_draft(raw: dict[str, Any], pages: PageCount) -> MeetingDraft:
    """검증된 raw dict를 MeetingDraft dataclass로 변환."""
    summary_items = tuple(
        SummaryItem(
            text=_strip_marker(item["text"]),
            subs=tuple(
                SubItem(
                    text=_strip_marker(sub["text"]),
                    detail=_strip_marker(sub.get("detail", "")) or None,
                )
                for sub in item.get("subs", [])
            ),
        )
        for item in raw.get("summary_items", [])
    )
    detail_items = tuple(
        DetailItem(
            text=_strip_marker(item["text"]),
            subs=tuple(
                SubItem(
                    text=_strip_marker(sub["text"]),
                    detail=_strip_marker(sub.get("detail", "")) or None,
                )
                for sub in item.get("subs", [])
            ),
        )
        for item in raw.get("detail_items", [])
    )
    next_steps = tuple(
        NextStepItem(
            text=_strip_marker(step["text"]),
            date=step.get("date", ""),
        )
        for step in raw.get("next_steps", [])
    )
    return MeetingDraft(
        title=raw["title"].strip(),
        date=raw["date"].strip(),
        department=raw["department"].strip(),
        place=raw["place"].strip(),
        attendees=tuple(a.strip() for a in raw["attendees"]),
        datetime_place=raw["datetime_place"].strip(),
        attendees_str=raw["attendees_str"].strip(),
        summary_items=summary_items,
        detail_items=detail_items,
        next_steps=next_steps,
        pages=pages,
    )


def _split_into_chunks(text: str, chunk_size: int) -> list[str]:
    """텍스트를 chunk_size 이하의 청크로 분할.

    우선순위: 줄바꿈 → 문장 종결부(. ! ?) → 강제 글자수 분할.
    STT 결과처럼 줄바꿈이 없는 긴 텍스트도 처리한다.
    """
    # 줄바꿈 기준 1차 분할
    lines = text.splitlines(keepends=True)

    # 한 줄이 chunk_size를 초과하면 문장 경계로 다시 분할
    expanded: list[str] = []
    for line in lines:
        if len(line) <= chunk_size:
            expanded.append(line)
        else:
            # 문장 종결 기호 뒤에서 분리 (마침표/느낌표/물음표 + 공백 or 종결)
            parts = re.split(r"(?<=[.!?])\s+", line)
            sub = ""
            for part in parts:
                if len(sub) + len(part) + 1 > chunk_size and sub:
                    expanded.append(sub.rstrip())
                    sub = part
                else:
                    sub = (sub + " " + part).lstrip() if sub else part
            if sub.strip():
                expanded.append(sub.rstrip())

    # 분할된 조각들을 chunk_size 이하로 묶기
    chunks: list[str] = []
    current = ""
    for seg in expanded:
        if len(current) + len(seg) > chunk_size and current:
            chunks.append(current.rstrip())
            current = seg
        else:
            current += seg
    if current.strip():
        chunks.append(current.rstrip())

    # 여전히 chunk_size 초과하는 청크는 강제 글자수 분할
    result: list[str] = []
    for chunk in chunks:
        while len(chunk) > chunk_size:
            result.append(chunk[:chunk_size])
            chunk = chunk[chunk_size:]
        if chunk.strip():
            result.append(chunk)

    return result or [text[:chunk_size]]


class MeetingDraftGenerator:
    """녹취록 → MeetingDraft 변환기. LLM 호출 1회 + 글자수 위반 시 1회 재시도."""

    _validator: Draft202012Validator

    def __init__(
        self,
        agent: _ChatAgentLike,
        *,
        max_retries: int = 1,
        custom_system_prompt: str = "",
    ) -> None:
        self._agent = agent
        self._max_retries = max_retries
        self._custom_system_prompt = custom_system_prompt
        self._validator = Draft202012Validator(MEETING_DRAFT_SCHEMA)

    async def _summarize_chunk(self, chunk: str, idx: int, total: int) -> str:
        """청크 하나를 글머리 목록으로 요약. 실패 시 원본 청크 앞부분 반환."""
        user_prompt = CHUNK_SUMMARY_USER_TEMPLATE.format(
            chunk_idx=idx + 1,
            total_chunks=total,
            chunk=chunk,
        )
        try:
            raw = await self._agent.complete_json(
                system_prompt=CHUNK_SUMMARY_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_schema=CHUNK_BULLETS_SCHEMA,
                max_tokens=1024,
                temperature=0.1,
                timeout_seconds=90.0,
            )
            points: list[str] = raw.get("points", [])
            return "\n".join(f"- {p.strip()}" for p in points if p.strip())
        except Exception as exc:
            logger.warning(f"청크 {idx + 1}/{total} 요약 실패: {exc} — 원본 앞부분 사용")
            return chunk[:600]

    async def _chunk_and_summarize(
        self,
        transcript: str,
        progress_cb: ProgressCallback | None = None,
    ) -> str:
        """긴 녹취록을 청크 분할 → 각 요약 → 합산 후 반환."""
        chunks = _split_into_chunks(transcript, _CHUNK_SIZE_CHARS)
        logger.info(f"녹취록 청크 분할: {len(transcript)}자 → {len(chunks)}개 청크")
        if progress_cb:
            await progress_cb(
                "chunk_start", f"녹취록이 깁니다. {len(chunks)}개 구간으로 나눠 요약합니다..."
            )
        summaries: list[str] = []
        for i, chunk in enumerate(chunks):
            if progress_cb:
                await progress_cb("chunk", f"녹취록 구간 요약 중... ({i + 1}/{len(chunks)})")
            summary = await self._summarize_chunk(chunk, i, len(chunks))
            summaries.append(summary)
        combined = "\n\n".join(f"[구간 {i + 1}]\n{s}" for i, s in enumerate(summaries))
        logger.info(f"청크 요약 완료: {len(combined)}자 압축본 생성")
        return combined

    async def _summarize_chunk_plain(self, chunk: str, idx: int, total: int) -> str:
        """청크 하나를 플레인 텍스트 글머리로 요약. JSON 모드 없이 호출."""
        user_prompt = (
            f"다음은 회의 녹취의 {idx + 1}/{total} 구간입니다.\n"
            "핵심 내용을 8~12개의 짧은 글머리로 요약하세요. 각 항목은 한 줄로 작성하고, "
            "수치·날짜·담당자는 빠뜨리지 마세요.\n\n"
            f"녹취 구간:\n'''\n{chunk}\n'''"
        )
        try:
            text = await self._agent.complete_text(
                system_prompt="회의 녹취록 일부를 읽고 핵심 내용을 글머리로 요약하는 역할입니다. 간결하게 작성하세요.",
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.2,
                timeout_seconds=120.0,
            )
            return text or chunk[:400]
        except Exception as exc:
            logger.warning(f"청크 {idx + 1}/{total} 텍스트 요약 실패: {exc} — 원본 앞부분 사용")
            return chunk[:400]

    async def summarize_to_text(
        self,
        transcript: str,
        progress_cb: ProgressCallback | None = None,
        pages: PageCount = 1,
    ) -> str:
        """녹취록 → 회의록 텍스트 변환 (Step 2).

        JSON 스키마 없이 plain text 출력. 짧으면 직접 요약, 길면 청크 분할 후 합산.
        pages=2 이면 상세하게, pages=1 이면 간결하게 작성.
        """
        if len(transcript) > _TRANSCRIPT_DIRECT_MAX_CHARS:
            chunks = _split_into_chunks(transcript, _CHUNK_SIZE_CHARS)
            if progress_cb:
                await progress_cb(
                    "chunk_start", f"녹취록을 {len(chunks)}개 구간으로 나눠 요약합니다..."
                )
            summaries: list[str] = []
            for i, chunk in enumerate(chunks):
                if progress_cb:
                    await progress_cb("chunk", f"구간 요약 중... ({i + 1}/{len(chunks)})")
                summaries.append(await self._summarize_chunk_plain(chunk, i, len(chunks)))
            transcript = "\n\n".join(f"[구간 {i + 1}]\n{s}" for i, s in enumerate(summaries))
            logger.info(f"청크 요약 완료: {len(transcript)}자 압축본 생성")

        if progress_cb:
            await progress_cb("generate", "회의록 작성 중...")

        if pages == 2:
            volume_instruction = (
                "분량 목표: A4 2페이지 분량. 논의 흐름, 쟁점, 근거, 세부 결정 사항까지 상세히 기술하세요. "
                "주요내용 ○ 항목 합계 8~9개."
            )
        else:
            volume_instruction = (
                "분량 목표: A4 1페이지 분량. 핵심 논의 사항과 결정 사항 위주로 간결하게 정리하세요. "
                "주요내용 ○ 항목 합계 3~4개."
            )

        # 사용자 커스텀 지침(M_17)을 2단계에도 적용한다 — 기존엔 3단계(JSON 생성)에만
        # 적용돼 사용자가 "설정한 기준이 동작하지 않는다"고 느꼈다 (E-41).
        # 커스텀 지침에는 JSON 출력 규칙이 포함될 수 있으므로 출력 형식만 오버라이드.
        base_rules = self._custom_system_prompt.strip() or SYSTEM_PROMPT
        step2_system = (
            f"{base_rules}\n\n"
            "## 이번 작업의 출력 형식 (위 JSON 규칙보다 우선)\n"
            "이번에는 JSON이 아니라 사람이 읽고 수정할 수 있는 개조식 보고서 텍스트를 출력합니다.\n"
            "위의 위계·글자수·톤 규칙(특히 개조식 종결: -음/-함/명사형)은 그대로 적용하되,\n"
            "아래 레이아웃의 plain text로만 출력하세요 (마크다운 기호 ​#, **, ``` 금지):\n\n"
            "회의 제목\n"
            "[개요]\n"
            "○ 일시·장소 : YYYY.MM.DD. HH:MM / 장소 (녹취에 없으면 날짜만)\n"
            "○ 참 석 자 : 이름1, 이름2 (녹취에 없으면 생략)\n"
            "○ 회의 목적·배경 (개조식 한 줄)\n"
            "\n"
            "[주요내용]\n"
            "○ 주요 논의·결정 사항 (개조식)\n"
            " - 부연 설명 (필요 시)\n"
            "  * 구체적 근거·일정·수치 (가능하면)\n"
            "\n"
            "[향후계획]\n"
            "○ 향후 조치 사항 (M.DD.)\n"
        )

        user_prompt = (
            f"다음 회의 녹취록을 개조식 회의 결과 보고 텍스트로 정리하세요.\n"
            f"{volume_instruction}\n"
            "녹취록에 나온 수치·날짜·담당자는 빠뜨리지 말고 반영하세요.\n\n"
            f"녹취록:\n'''\n{transcript}\n'''"
        )
        try:
            result = await self._agent.complete_text(
                system_prompt=step2_system,
                user_prompt=user_prompt,
                max_tokens=3000 if pages == 2 else 2048,
                temperature=0.3,
                timeout_seconds=180.0,
            )
        except TimeoutError:
            raise
        if not result:
            raise ValueError("LLM이 회의록을 생성하지 못했습니다. 잠시 후 다시 시도해주세요.")
        return result

    async def generate(
        self,
        transcript: str,
        pages: PageCount,
        progress_cb: ProgressCallback | None = None,
    ) -> MeetingDraft:
        """녹취록을 MeetingDraft로 변환. 실패 시 MeetingDraftError 또는 MeetingDraftValidationError raise.

        녹취록이 _TRANSCRIPT_DIRECT_MAX_CHARS 초과 시 청크 분할 → 요약 → 합산 후 처리.
        오디오 STT 결과 및 직접 입력 텍스트 모두 동일하게 처리된다.
        """
        if len(transcript) > _TRANSCRIPT_DIRECT_MAX_CHARS:
            transcript = await self._chunk_and_summarize(transcript, progress_cb)

        if progress_cb:
            await progress_cb("generate", "회의록 초안 작성 중...")

        import datetime

        today = datetime.date.today().strftime("%Y.%m.%d.")
        volume_guide = VOLUME_GUIDE_1PAGE if pages == 1 else VOLUME_GUIDE_2PAGE
        user_prompt = USER_PROMPT_TEMPLATE.format(
            pages=pages,
            volume_guide=volume_guide,
            transcript=transcript,
            today_date=today,
        )

        effective_system_prompt = self._custom_system_prompt.strip() or SYSTEM_PROMPT
        last_error: str = ""
        raw: dict[str, Any] | None = None

        for attempt in range(self._max_retries + 1):
            attempt_user_prompt = user_prompt
            if attempt > 0 and last_error:
                attempt_user_prompt = (
                    f"이전 응답이 규칙을 위반했습니다: {last_error}. 다시 시도해 주세요.\n\n"
                    + user_prompt
                )
                logger.info(f"MeetingDraftGenerator 재시도 #{attempt}: {last_error[:100]}")

            try:
                raw = await self._agent.complete_json(
                    system_prompt=effective_system_prompt,
                    user_prompt=attempt_user_prompt,
                    json_schema=MEETING_DRAFT_SCHEMA,
                    max_tokens=4096,
                    temperature=0.2,
                    timeout_seconds=180.0,
                )
                logger.debug(f"complete_json 성공 (attempt={attempt})")
            except ValueError as exc:
                last_error = f"비-JSON 응답: {exc}"
                logger.warning(f"complete_json 비-JSON (attempt={attempt}): {exc}")
                if attempt >= self._max_retries:
                    raise MeetingDraftError(
                        f"LLM이 유효한 JSON을 반환하지 않았습니다: {exc}"
                    ) from exc
                continue
            except TimeoutError as exc:
                last_error = "LLM 응답 시간 초과 (180초)"
                logger.error(f"complete_json 시간 초과 (attempt={attempt}): {last_error}")
                if attempt >= self._max_retries:
                    raise MeetingDraftError(
                        "LLM 응답 시간 초과: 회의록 생성에 너무 오래 걸렸습니다. 잠시 후 다시 시도해 주세요."
                    ) from exc
                continue
            except Exception as exc:
                last_error = f"LLM 호출 실패: {type(exc).__name__}: {exc}"
                logger.error(f"complete_json 호출 실패 (attempt={attempt}): {last_error}")
                if attempt >= self._max_retries:
                    raise MeetingDraftError(f"LLM 호출 실패: {type(exc).__name__}: {exc}") from exc
                continue

            # 형식 일탈 정규화 후 JSON Schema 검증
            assert raw is not None
            raw = _normalize_raw_draft(raw)
            schema_errors = sorted(self._validator.iter_errors(raw), key=lambda e: e.absolute_path)
            if schema_errors:
                first = schema_errors[0]
                path = "/".join(str(p) for p in first.absolute_path) or "<root>"
                last_error = f"JSON Schema 위반 at {path}: {first.message}"
                logger.warning(f"JSON Schema 위반 (attempt={attempt}): {last_error}")
                if attempt >= self._max_retries:
                    raise MeetingDraftValidationError(
                        f"LLM 응답이 JSON Schema를 위반했습니다 (max_retries 소진): {last_error}"
                    )
                continue

            # 글자수·분량 검증
            violations = _check_length_violations(raw, pages)
            if violations:
                last_error = "; ".join(violations[:3])
                logger.warning(f"글자수·분량 위반 (attempt={attempt}): {violations}")
                if attempt >= self._max_retries:
                    # 재시도 후에도 위반 시 경고 후 통과 (스펙 §7.4)
                    logger.warning(
                        f"글자수·분량 위반이 있으나 max_retries 소진 후 통과: {violations}"
                    )
                    break
                continue

            # 모든 검증 통과
            break

        assert raw is not None
        draft = _dict_to_draft(raw, pages)
        logger.info(
            f"MeetingDraft 생성 완료: title={draft.title!r}, pages={pages}, "
            f"summary={len(draft.summary_items)}, detail={len(draft.detail_items)}, "
            f"next_steps={len(draft.next_steps)}"
        )
        return draft
