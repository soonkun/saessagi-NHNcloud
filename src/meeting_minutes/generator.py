# src/meeting_minutes/generator.py
"""M_13 MeetingMinutes — LLM 호출 및 MeetingDraft 생성기."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .errors import MeetingDraftError, MeetingDraftValidationError
from .prompts import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    VOLUME_GUIDE_1PAGE,
    VOLUME_GUIDE_2PAGE,
)
from .schemas import MEETING_DRAFT_SCHEMA
from .types import DetailItem, MeetingDraft, NextStepItem, PageCount, SubItem, SummaryItem

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


def _dict_to_draft(raw: dict[str, Any], pages: PageCount) -> MeetingDraft:
    """검증된 raw dict를 MeetingDraft dataclass로 변환."""
    summary_items = tuple(
        SummaryItem(
            text=item["text"].strip(),
            subs=tuple(
                SubItem(
                    text=sub["text"].strip(),
                    detail=sub.get("detail", "").strip() or None,
                )
                for sub in item.get("subs", [])
            ),
        )
        for item in raw.get("summary_items", [])
    )
    detail_items = tuple(
        DetailItem(
            text=item["text"].strip(),
            subs=tuple(
                SubItem(
                    text=sub["text"].strip(),
                    detail=sub.get("detail", "").strip() or None,
                )
                for sub in item.get("subs", [])
            ),
        )
        for item in raw.get("detail_items", [])
    )
    next_steps = tuple(
        NextStepItem(
            text=step["text"].strip(),
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


class MeetingDraftGenerator:
    """녹취록 → MeetingDraft 변환기. LLM 호출 1회 + 글자수 위반 시 1회 재시도."""

    _validator: Draft202012Validator

    def __init__(
        self,
        agent: _ChatAgentLike,
        *,
        max_retries: int = 1,
    ) -> None:
        self._agent = agent
        self._max_retries = max_retries
        self._validator = Draft202012Validator(MEETING_DRAFT_SCHEMA)

    async def generate(
        self,
        transcript: str,
        pages: PageCount,
    ) -> MeetingDraft:
        """녹취록을 MeetingDraft로 변환. 실패 시 MeetingDraftError 또는 MeetingDraftValidationError raise."""
        volume_guide = VOLUME_GUIDE_1PAGE if pages == 1 else VOLUME_GUIDE_2PAGE
        user_prompt = USER_PROMPT_TEMPLATE.format(
            pages=pages,
            volume_guide=volume_guide,
            transcript=transcript,
        )

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
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=attempt_user_prompt,
                    json_schema=MEETING_DRAFT_SCHEMA,
                    max_tokens=4096,
                    temperature=0.2,
                    timeout_seconds=60.0,
                )
                logger.debug(f"complete_json 성공 (attempt={attempt})")
            except ValueError as exc:
                last_error = f"비-JSON 응답: {exc}"
                logger.warning(f"complete_json 비-JSON (attempt={attempt}): {exc}")
                if attempt >= self._max_retries:
                    raise MeetingDraftError(
                        f"LLM이 유효한 JSON을 반환하지 않았습니다 (max_retries 소진): {exc}"
                    ) from exc
                continue
            except Exception as exc:
                last_error = f"LLM 호출 실패: {exc}"
                logger.error(f"complete_json 호출 실패 (attempt={attempt}): {exc}")
                if attempt >= self._max_retries:
                    raise MeetingDraftError(f"LLM 호출 실패 (max_retries 소진): {exc}") from exc
                continue

            # JSON Schema 검증
            assert raw is not None
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
