from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from calendar_service.errors import EventNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


# ---------- Pydantic models ----------


# CR-70: 시작/종료 시각으로 등록한다.
#
# 저장은 여전히 `duration_minutes`다(DB 스키마·도구 인자를 그대로 두는 쪽이 안전하다).
# 대신 **입력을 종료 시각으로** 받고 여기서 분으로 환산한다 — 사람은 "9시~18시"로
# 생각하고 "540분"으로 생각하지 않는다.
def _duration_from(start: str, end: str | None, fallback: int | None) -> int:
    """(start, end) → 분. `end`가 없으면 fallback, 그것도 없으면 60분."""
    if end:
        try:
            s = datetime.fromisoformat(start)
            e = datetime.fromisoformat(end)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"end must be ISO 8601: {end!r}") from exc
        minutes = int((e - s).total_seconds() // 60)
        if minutes <= 0:
            raise HTTPException(status_code=422, detail="종료 시각이 시작 시각보다 뒤여야 합니다.")
        return minutes
    return fallback if fallback is not None else 60


class EventResponse(BaseModel):
    id: int
    title: str
    start: str
    end: str
    duration_minutes: int
    description: str | None


class CreateEventRequest(BaseModel):
    title: str
    start: str
    # 둘 중 하나면 된다 — `end`가 있으면 그것으로 길이를 정한다 (CR-70).
    end: str | None = None
    duration_minutes: int | None = None
    description: str | None = None

    @field_validator("start")
    @classmethod
    def parse_start(cls, v: str) -> str:
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError(f"start must be ISO 8601, got: {v!r}")
        return v


class UpdateEventRequest(BaseModel):
    title: str | None = None
    start: str | None = None
    end: str | None = None
    duration_minutes: int | None = None
    description: str | None = None

    @field_validator("start")
    @classmethod
    def parse_start(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError(f"start must be ISO 8601, got: {v!r}")
        return v


# ---------- helpers ----------


def _get_service(request: Request) -> Any:
    ctx = request.app.state.service_context
    svc = getattr(ctx, "calendar_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="calendar_service unavailable")
    return svc


def _event_to_response(event: Any) -> EventResponse:
    from datetime import timedelta

    end = event.start + timedelta(minutes=event.duration_minutes)
    return EventResponse(
        id=event.id,
        title=event.title,
        start=event.start.isoformat(),
        end=end.isoformat(),
        duration_minutes=event.duration_minutes,
        description=event.description,
    )


# ---------- endpoints ----------


@router.get("/events", response_model=list[EventResponse])
async def list_events(
    request: Request,
    start: str | None = None,
    end: str | None = None,
) -> list[EventResponse]:
    svc = _get_service(request)
    try:
        if start is not None:
            start_dt = datetime.fromisoformat(start)
        else:
            from datetime import timezone

            start_dt = datetime.fromtimestamp(0, tz=timezone.utc)

        if end is not None:
            end_dt = datetime.fromisoformat(end)
        else:
            from datetime import timezone

            end_dt = datetime(9999, 12, 31, tzinfo=timezone.utc)

        events = svc.get_events(start=start_dt, end=end_dt)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("list_events error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return [_event_to_response(e) for e in events]


@router.post("/events", response_model=EventResponse, status_code=201)
async def create_event(request: Request, body: CreateEventRequest) -> EventResponse:
    svc = _get_service(request)
    try:
        start_dt = datetime.fromisoformat(body.start)
        event = svc.add_event(
            title=body.title,
            start=start_dt,
            duration_minutes=_duration_from(body.start, body.end, body.duration_minutes),
            description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        # 우리가 만든 422(종료 시각 오류 등)를 500으로 바꾸지 않는다 (CR-70).
        # `except Exception`이 HTTPException까지 삼켜, 사용자에게 "서버 오류"로 보였다.
        raise
    except Exception as exc:
        logger.error("create_event error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return _event_to_response(event)


@router.get("/events/{event_id}", response_model=EventResponse)
async def get_event(request: Request, event_id: int) -> EventResponse:
    svc = _get_service(request)
    try:
        event = svc.get_event(event_id)
    except Exception as exc:
        logger.error("get_event error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if event is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return _event_to_response(event)


@router.put("/events/{event_id}", response_model=EventResponse)
async def update_event(request: Request, event_id: int, body: UpdateEventRequest) -> EventResponse:
    svc = _get_service(request)
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title
    if body.start is not None:
        fields["start"] = datetime.fromisoformat(body.start)
    if body.end is not None:
        # 종료 시각만 바꾸는 경우도 있다 — 시작은 기존 값을 쓴다 (CR-70).
        base = body.start or next(
            (e.start.isoformat() for e in svc.list_events() if e.id == event_id), None
        )
        if base is None:
            raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
        fields["duration_minutes"] = _duration_from(base, body.end, None)
    elif body.duration_minutes is not None:
        fields["duration_minutes"] = body.duration_minutes
    if body.description is not None:
        fields["description"] = body.description

    try:
        event = svc.update_event(event_id, **fields)
    except HTTPException:
        raise
    except Exception as exc:
        if isinstance(exc, EventNotFoundError):
            raise HTTPException(status_code=404, detail=f"event {event_id} not found")
        logger.error("update_event error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return _event_to_response(event)


@router.delete("/events/{event_id}")
async def delete_event(request: Request, event_id: int) -> dict[str, bool]:
    svc = _get_service(request)
    try:
        deleted = svc.delete_event(event_id)
    except Exception as exc:
        logger.error("delete_event error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if not deleted:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    return {"ok": True}
