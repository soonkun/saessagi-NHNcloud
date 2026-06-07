# src/tool_router/router.py
"""ToolRouter — Gemma tool_call → 로컬 파이썬 핸들러 디스패처."""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

from .errors import ScreenshotCaptureError
from .schemas import ALL_TOOL_SCHEMAS
from .types import ToolResult, ToolSpec

if TYPE_CHECKING:
    from .screenshot import ScreenshotService
    from avatar_state import AvatarState
    from meeting_minutes.service import MeetingMinutesService
    from knowledge.service import KnowledgeService

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")


def _service_unavail(tool_name: str) -> ToolResult:
    logger.warning("service_unavailable: %s", tool_name)
    return ToolResult(
        ok=False,
        error=f"service_unavailable: {tool_name}",
        error_code="service_unavailable",
    )


class ToolRouter:
    """Gemma tool_call → 로컬 파이썬 핸들러 디스패처.

    생성자 주입 서비스가 None이면 해당 툴은 런타임에 service_unavailable을 반환한다.
    (초기화는 성공하되, 호출 시 실패하는 정책 — 부트 시 일부 서비스가 없어도 나머지 툴은 동작)
    """

    LOCAL_TOOL_NAMES: frozenset[str] = frozenset(
        {
            "add_event",
            "get_events",
            "search_docs",
            "take_screenshot",
            "create_meeting_minutes",
            "save_knowledge_note",
        }
    )

    def __init__(
        self,
        calendar: Any,  # CalendarService | None
        rag: Any,  # RagService | None
        screenshot: "ScreenshotService | None" = None,
        meeting_minutes: "MeetingMinutesService | None" = None,
        avatar_state: "AvatarState | None" = None,
        knowledge: "KnowledgeService | None" = None,
    ) -> None:
        """
        Args:
            calendar: M_09 CalendarService 인스턴스. None이면 add_event/get_events가 service_unavailable.
            rag: M_07 RagService 인스턴스. None이면 search_docs가 service_unavailable.
            screenshot: M_05b 내부 ScreenshotService 인스턴스. None이면 take_screenshot이 service_unavailable (비-Windows).
            meeting_minutes: M_13 MeetingMinutesService 인스턴스. None이면 create_meeting_minutes가 service_unavailable.
            avatar_state: M_08 AvatarState 인스턴스. None이면 아바타 상태 전환 스킵.
            knowledge: M_15 KnowledgeService 인스턴스. None이면 save_knowledge_note가 service_unavailable.
        """

        self._calendar = calendar
        self._rag = rag
        self._screenshot = screenshot
        self._meeting_minutes = meeting_minutes
        self._avatar_state = avatar_state
        self._knowledge = knowledge

        # JSON Schema Validator를 __init__ 시 4개 컴파일 후 재사용
        self._validators: dict[str, Draft202012Validator] = {}
        for spec in ALL_TOOL_SCHEMAS:
            name: str = spec["function"]["name"]
            params: dict[str, Any] = spec["function"]["parameters"]
            self._validators[name] = Draft202012Validator(params)

        logger.info("ToolRouter 초기화 완료. 등록 툴: %s", sorted(self._validators.keys()))

    def tool_specs(self) -> list[ToolSpec]:
        """5개 툴의 OpenAI function-calling JSON schema 리스트를 반환.

        반환 리스트는 **호출마다 새 사본**(list copy)이다. 호출 측의 수정이 내부 상태를
        오염시키지 않도록 보호한다.
        """
        return list(ALL_TOOL_SCHEMAS)

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """tool_call 이름·인자를 받아 핸들러를 호출.

        항상 ToolResult를 반환하며 예외를 raise하지 않는다 (CancelledError 제외).
        """
        # (1) 이름 화이트리스트
        if name not in self.LOCAL_TOOL_NAMES:
            logger.info("unknown_tool: %s", name)
            return ToolResult(
                ok=False,
                error=f"unknown_tool: {name}",
                error_code="unknown_tool",
            )

        # (2) arguments 타입 검사
        if not isinstance(arguments, dict):
            logger.warning(
                "invalid_arguments: arguments must be dict, got %s",
                type(arguments).__name__,
            )
            return ToolResult(
                ok=False,
                error=f"arguments must be dict, got {type(arguments).__name__}",
                error_code="invalid_arguments",
            )

        # (3) JSON Schema 검증 (미리 컴파일된 Validator 재사용)
        validator = self._validators[name]
        errors = sorted(validator.iter_errors(arguments), key=lambda e: e.absolute_path)
        if errors:
            first = errors[0]
            path = "/".join(str(p) for p in first.absolute_path) or "<root>"
            msg = f"invalid_arguments at {path}: {first.message}"
            logger.info("validation error for %s: %s", name, msg)
            return ToolResult(
                ok=False,
                error=msg,
                error_code="invalid_arguments",
            )

        # (4) 핸들러 분기 및 서비스 존재 체크
        try:
            if name == "add_event":
                if self._calendar is None:
                    return _service_unavail("add_event")
                return await self._handle_add_event(arguments)

            elif name == "get_events":
                if self._calendar is None:
                    return _service_unavail("get_events")
                return await self._handle_get_events(arguments)

            elif name == "search_docs":
                if self._rag is None:
                    return _service_unavail("search_docs")
                return await self._handle_search_docs(arguments)

            elif name == "take_screenshot":
                return await self._handle_take_screenshot(arguments)

            elif name == "create_meeting_minutes":
                return await self._handle_create_meeting_minutes(arguments)

            else:  # save_knowledge_note
                if self._knowledge is None:
                    return _service_unavail("save_knowledge_note")
                return await self._handle_save_knowledge_note(arguments)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Tool handler raised: %s", name)
            return ToolResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                error_code="handler_exception",
            )

    # ------------------------------------------------------------------ #
    # 내부 핸들러
    # ------------------------------------------------------------------ #

    async def _handle_add_event(self, args: dict[str, Any]) -> ToolResult:
        """add_event 핸들러."""
        start_str: str = args["start"]
        start_dt = datetime.fromisoformat(start_str)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=_KST)

        title: str = args["title"]
        duration_minutes: int = args["duration_minutes"]
        description: str | None = args.get("description")

        event = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._calendar.add_event(title, start_dt, duration_minutes, description),
        )

        # event 객체를 payload로 직렬화
        start_val = event.start
        if isinstance(start_val, datetime):
            start_val = start_val.isoformat()

        payload: dict[str, Any] = {
            "id": event.id,
            "title": event.title,
            "start": start_val,
            "duration_minutes": event.duration_minutes,
            "description": getattr(event, "description", None),
        }
        logger.info("add_event 성공: id=%s, title=%r", event.id, event.title)
        return ToolResult(ok=True, payload=payload)

    async def _handle_get_events(self, args: dict[str, Any]) -> ToolResult:
        """get_events 핸들러."""
        start_str: str = args["start"]
        end_str: str = args["end"]
        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str)

        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=_KST)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=_KST)

        # start > end → 빈 리스트 반환 (CalendarService 호출 없음)
        if start_dt > end_dt:
            logger.info("get_events: start > end, 빈 결과 반환.")
            return ToolResult(ok=True, payload={"count": 0, "events": []})

        events = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._calendar.get_events(start_dt, end_dt),
        )

        serialized = []
        for evt in events:
            evt_start = evt.start
            if isinstance(evt_start, datetime):
                evt_start = evt_start.isoformat()
            serialized.append(
                {
                    "id": evt.id,
                    "title": evt.title,
                    "start": evt_start,
                    "duration_minutes": evt.duration_minutes,
                    "description": getattr(evt, "description", None),
                }
            )

        logger.info("get_events 성공: %d건 반환", len(serialized))
        return ToolResult(ok=True, payload={"count": len(serialized), "events": serialized})

    async def _handle_search_docs(self, args: dict[str, Any]) -> ToolResult:
        """search_docs 핸들러 — 문서 + 업무 노트 hybrid 검색.

        문서가 압도적으로 많을 때 업무 노트(__knowledge__)가 top_k에서 밀려나는 문제를
        방지하기 위해, category 미지정 시 두 풀에서 각각 검색해 결과를 머지한다:
          - 일반 문서 검색 (모든 카테고리, 단 __knowledge__ 제외)
          - 노트 검색 (__knowledge__ 카테고리만)
        둘 다 받아서 score 내림차순 정렬 후 top_k 반환.

        category가 명시되면 그대로 단일 검색.
        """
        query: str = args["query"]
        top_k: int = args.get("top_k", 8)
        category: str | None = args.get("category")

        loop = asyncio.get_running_loop()

        async def _retrieve(cat: str | None, k: int) -> Any:
            return await loop.run_in_executor(
                None, lambda: self._rag.retrieve(query, k, cat)
            )

        if category is not None:
            # 명시 카테고리 — 단일 검색
            primary = await _retrieve(category, top_k)
            note_hits: list[Any] = []
            doc_hits: list[Any] = list(primary.hits)
        else:
            # 미지정 — hybrid: 노트 풀 + 일반 문서 풀 각각 검색
            note_retrieval, doc_retrieval = await asyncio.gather(
                _retrieve("__knowledge__", max(3, top_k // 2)),
                _retrieve(None, top_k),
            )
            note_hits = [h for h in note_retrieval.hits]
            # 일반 문서 풀에서 __knowledge__ 카테고리 hit은 제외 (중복 방지)
            doc_hits = [
                h for h in doc_retrieval.hits
                if getattr(h, "category", None) != "__knowledge__"
            ]
            primary = doc_retrieval

        # 머지: 노트 우선 보장 + score 내림차순 정렬
        all_hits = note_hits + doc_hits
        all_hits.sort(key=lambda h: float(getattr(h, "score", 0.0)), reverse=True)

        # 노트가 hit이 있으면 최소 1개는 top_k 안에 보장 (밀려나도 강제 포함)
        if note_hits:
            best_note = max(note_hits, key=lambda h: float(getattr(h, "score", 0.0)))
            top_slice = all_hits[:top_k]
            if best_note not in top_slice:
                all_hits = [best_note] + [h for h in all_hits if h is not best_note]

        all_hits = all_hits[:top_k]

        hits_payload = []
        for hit in all_hits:
            citation = self._rag.format_citation(hit)
            hits_payload.append(
                {
                    "doc_id": getattr(hit, "doc_id", None),
                    "doc_name": getattr(hit, "doc_name", None),
                    "page": getattr(hit, "page", None),
                    "section": getattr(hit, "section", None),
                    "chunk_id": getattr(hit, "chunk_id", None),
                    "text": getattr(hit, "text", ""),
                    "score": float(getattr(hit, "score", 0.0)),
                    "citation": citation,
                    "is_note": getattr(hit, "category", None) == "__knowledge__",
                }
            )

        found = bool(hits_payload)
        no_match_reason: str | None = None
        if not found:
            no_match_reason = (
                getattr(primary, "no_match_reason", None)
                or "등록된 문서/노트에서 답을 찾지 못했습니다"
            )
            logger.info("search_docs: 결과 없음. query=%r", query)
        else:
            note_count = sum(1 for h in hits_payload if h["is_note"])
            logger.info(
                "search_docs 성공: %d건 반환 (노트=%d, 문서=%d, query=%r)",
                len(hits_payload), note_count, len(hits_payload) - note_count, query[:50],
            )

        return ToolResult(
            ok=True,
            payload={
                "found": found,
                "no_match_reason": no_match_reason,
                "hits": hits_payload,
            },
        )

    async def _handle_take_screenshot(self, args: dict[str, Any]) -> ToolResult:
        """take_screenshot 핸들러."""
        if self._screenshot is None:
            return _service_unavail("take_screenshot")

        continuous: bool = args.get("continuous", False)
        interval: float = args.get("interval_seconds", 5.0)

        if not continuous:
            try:
                data_url = await self._screenshot.capture_once()
            except ScreenshotCaptureError as exc:
                logger.error("take_screenshot 단건 캡처 실패: %s", exc)
                return ToolResult(
                    ok=False,
                    error=str(exc),
                    error_code="screenshot_failed",
                )
            return ToolResult(
                ok=True,
                payload={"mode": "single", "image": data_url},
            )

        # continuous=True
        if self._screenshot.is_continuous_running:
            logger.info("take_screenshot: 연속 모드 이미 실행 중.")
            return ToolResult(
                ok=True,
                payload={
                    "mode": "continuous",
                    "state": "already_running",
                    "interval_seconds": interval,
                },
            )

        # 연속 모드 시작
        try:
            await self._screenshot.start_continuous(interval, on_frame=self._on_continuous_frame)
        except asyncio.CancelledError:
            # start_continuous가 create_task 이후 CancelledError를 받은 경우
            # is_continuous_running이 True라면 task가 이미 생성된 것이므로 정리
            if self._screenshot.is_continuous_running:
                await self._screenshot.stop_continuous()
            raise

        return ToolResult(
            ok=True,
            payload={
                "mode": "continuous",
                "state": "started",
                "interval_seconds": interval,
            },
        )

    async def _handle_create_meeting_minutes(self, args: dict[str, Any]) -> ToolResult:
        """create_meeting_minutes 핸들러."""
        from meeting_minutes.tool import handle_create_meeting_minutes

        return await handle_create_meeting_minutes(  # type: ignore[no-any-return]
            self._meeting_minutes, args, avatar_state=self._avatar_state
        )

    async def _handle_save_knowledge_note(self, args: dict[str, Any]) -> ToolResult:
        """save_knowledge_note 핸들러 — 업무 지식 노트 저장."""
        title: str = args["title"]
        summary: str = args["summary"]
        tags: list[str] = args.get("tags", []) or []
        related_docs: list[str] = args.get("related_docs", []) or []

        try:
            note = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self._knowledge.create_note(  # type: ignore[union-attr]
                    title=title,
                    content=summary,
                    tags=tags,
                    related_docs=related_docs,
                ),
            )
        except Exception as exc:
            logger.exception("save_knowledge_note 저장 실패: %s", exc)
            return ToolResult(
                ok=False,
                error=f"노트 저장 실패: {exc}",
                error_code="handler_exception",
            )

        logger.info(
            "save_knowledge_note 성공: slug=%s, title=%s, related_docs=%d",
            note.slug, note.title, len(related_docs),
        )
        return ToolResult(
            ok=True,
            payload={
                "slug": note.slug,
                "title": note.title,
                "tags": list(note.tags),
                "related_docs": list(note.related_docs),
                "note_marker": f"[[note:{note.slug}]]",
                "alert": (
                    f"업무 노트 '{note.title}'(으)로 저장했습니다. "
                    f"답변 끝에 반드시 {{note_marker}} (={'[[note:' + note.slug + ']]'})를 한 번 포함하세요."
                ),
            },
        )

    async def _on_continuous_frame(self, data_url: str) -> None:
        """기본 연속 프레임 콜백 (log.debug만). M_01이 실제 on_frame을 주입한다."""
        logger.debug("연속 캡처 프레임 수신: data_url 길이=%d", len(data_url))
