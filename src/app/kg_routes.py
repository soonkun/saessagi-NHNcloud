# src/app/kg_routes.py
"""M_23 지식그래프 파이프라인 REST API.

기존 그래프 탭에 재인덱싱·중단 버튼이 이미 있는데 새 파이프라인만 CLI로 두면
사용자가 터미널을 열어야 한다(사용자 지적). 같은 자리에서 시작·중단·진행 확인이
되도록 노출한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

router = APIRouter(prefix="/api/kg", tags=["kg"])


def _svc(request: Request) -> Any:
    ctx = getattr(request.app.state, "service_context", None)
    svc = getattr(ctx, "kg_service", None) if ctx else None
    if svc is None:
        raise HTTPException(status_code=503, detail="지식그래프 파이프라인이 준비되지 않았습니다.")
    return svc


class StartReq(BaseModel):
    folder_id: str = ""
    doc_ids: list[str] | None = None
    limit: int = 0
    resume: bool = True
    chunks_per_document: int | None = None
    # 등록된 모든 폴더를 한 작업으로. 폴더를 하나씩 골라 여러 번 누르지 않아도 되게 한다.
    all_folders: bool = False
    # 추출이 정상 완료되면 6~9단계 구축까지 자동으로 잇는다 (CR-61).
    build_after: bool = False


@router.get("/folders")
async def folders(request: Request) -> dict[str, Any]:
    """폴더별 문서 수와 추출 완료 수 — UI에서 대상을 고르는 데 쓴다."""
    return {"folders": _svc(request).folders()}


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    return {"running": svc.running, "progress": svc.progress(), "stats": svc.stats()}


@router.post("/start")
async def start(body: StartReq, request: Request) -> dict[str, Any]:
    svc = _svc(request)
    result = await svc.start(
        folder_id=body.folder_id,
        doc_ids=body.doc_ids,
        limit=body.limit,
        resume=body.resume,
        chunks_per_document=body.chunks_per_document,
        all_folders=body.all_folders,
        build_after=body.build_after,
    )
    logger.info(
        f"KG 추출 시작 요청: folder={body.folder_id!r} all={body.all_folders} "
        f"limit={body.limit} build_after={body.build_after} → {result.get('started')}"
    )
    return result


@router.post("/stop")
async def stop(request: Request) -> dict[str, Any]:
    """진행 중인 청크가 끝나면 멈춘다 (강제 종료 아님)."""
    return await _svc(request).stop()


# ── 6~9단계 그래프 구축 (CR-61) ──────────────────────────────────────────────
#
# 추출 버튼만 있고 구축은 CLI로 두면 CR-60에서 지적받은 것과 같은 상황이 된다
# ("엔티티 추출 버튼이 있는데 왜 파이썬 코드를 주냐"). 같은 자리에서 구축까지 된다.


class BuildReq(BaseModel):
    folder_id: str = ""
    dry_run: bool = False
    # M_19 키워드 그래프 삭제는 되돌릴 수 없다. 기본은 끄고 호출자가 명시해야 한다.
    purge_legacy: bool = False


def _graph_store_factory(request: Request) -> Any:
    """graphrag 설정의 Neo4j 접속 정보를 그대로 쓴다 — 같은 인스턴스에 적재한다.

    **`ctx.config`가 아니라 `ctx.app_config`다 (E-98).** `ctx.config`는 벤더링된
    upstream `Config`라 최상위 필드가 `system_config/character_config/live_config`뿐이고
    `.app`이 없다. 새싹이의 `app:` 블록은 별도 `AppConfig`로 파싱돼 `ctx.app_config`에
    실린다. 예전 코드는 `cfg.app.graphrag`를 봐서 **언제나 None**을 반환했고, 그 결과
    "구축 시작" 버튼이 24분을 돌고 Neo4j에는 아무것도 쓰지 않은 채 COMPLETED가 됐다.
    """
    import os

    from kg.graph_store import KgGraphStore

    ctx = getattr(request.app.state, "service_context", None)
    g = getattr(getattr(ctx, "app_config", None), "graphrag", None)
    if g is None:
        logger.warning("KG 구축: graphrag 설정이 없어 Neo4j 스토어를 만들 수 없습니다")
        return None

    def make() -> KgGraphStore:
        return KgGraphStore(
            uri=g.neo4j_uri,
            user=g.neo4j_user,
            password=os.environ.get("SAESSAGI_NEO4J_PASSWORD") or g.neo4j_password,
            database=g.neo4j_database,
        )

    return make


@router.post("/build")
async def build(body: BuildReq, request: Request) -> dict[str, Any]:
    """6~9단계 실행 — LLM을 부르지 않으므로 GPU를 잡지 않는다."""
    svc = _svc(request)
    result = await svc.start_build(
        folder_id=body.folder_id,
        dry_run=body.dry_run,
        purge_legacy=body.purge_legacy,
        graph_store_factory=None if body.dry_run else _graph_store_factory(request),
    )
    logger.info(
        f"KG 그래프 구축 요청: folder={body.folder_id!r} dry_run={body.dry_run} "
        f"purge_legacy={body.purge_legacy} → {result.get('started')}"
    )
    return result


@router.get("/build/status")
async def build_status(request: Request) -> dict[str, Any]:
    svc = _svc(request)
    return {"running": svc.build_running, "progress": svc.build_progress()}


@router.post("/build/stop")
async def build_stop(request: Request) -> dict[str, Any]:
    """단계 경계에서 멈춘다. 끝난 단계 결과는 SQLite에 남는다."""
    return await _svc(request).stop_build()


@router.get("/review")
async def review(request: Request, limit: int = 50) -> dict[str, Any]:
    """검토 큐 — 블롭 감시·모호 판정에 걸린 정규 엔티티."""
    return {"items": _svc(request).review_queue(limit)}


@router.get("/report")
async def report(request: Request) -> dict[str, Any]:
    """10단계 관찰 리포트. **품질 증명이 아니다** (스펙 §9.1)."""
    return _svc(request).report()


# ── CR-64: doc_id → 표시용 제목 ───────────────────────────────────────────────
#
# 답변 본문의 인용 칩에 **파일명 대신 과제 제목**을 보여주기 위한 조회다.
# 벡터 스토어 스키마에는 제목이 없고(doc_id·doc_name·category·page·…), M_23 후보
# 저장소에만 있다. 실측 12,070건 전부 제목이 채워져 있다.
#
# 여기서 `projects` 모듈의 정제(E-93/E-99)를 그대로 태운다 — `(해당 시 작성)`,
# `주관과제명 …` 같은 서식 문구가 칩에 뜨면 안 된다. 같은 판정을 두 곳에 복사하지
# 않으려고 함수를 재사용한다.


class DocTitlesReq(BaseModel):
    doc_ids: list[str]


@router.post("/doc-titles")
async def doc_titles(body: DocTitlesReq, request: Request) -> dict[str, str]:
    """doc_id 목록 → 표시용 제목. 못 찾으면 그 id는 결과에서 빠진다(호출자가 폴백)."""
    from kg.identity import is_placeholder_title, strip_placeholder_prefix
    from kg.projects import title_from_doc_name

    ids = [d for d in dict.fromkeys(body.doc_ids) if d][:200]
    if not ids:
        return {}

    svc = _svc(request)
    store = svc._store  # noqa: SLF001 — 같은 앱 내부 조회
    out: dict[str, str] = {}
    for doc_id in ids:
        try:
            meta = store.get_document(doc_id)
        except Exception:
            meta = None
        if meta is None:
            continue
        title = (meta.title or "").strip()
        if is_placeholder_title(title):
            title = title_from_doc_name(meta.doc_name)
        else:
            stripped = strip_placeholder_prefix(title)
            if stripped:
                title = stripped
        if not title:
            title = title_from_doc_name(meta.doc_name)
        if title:
            out[doc_id] = title
    return out
