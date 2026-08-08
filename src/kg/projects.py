# src/kg/projects.py
"""M_23 과제(Project) 동일성 — 문서를 과제로 묶는다 (스펙 §5.2).

**이 모듈은 기대만큼 일하지 못한다. 그 이유를 여기 적어 둔다.**

스펙 §1은 "계획한 연구와 실제 수행한 연구가 어떻게 다른가"를 답해야 할 질문으로 꼽았고,
그러려면 RFP(계획)와 완결보고서(실적)가 같은 Project 노드에 붙어야 한다. 실측 결과:

    project_no 있는 문서   3,035 / 6,121   (2,656개로 흩어짐 — 한 과제 최대 3문서)
    RFP 1,984건            project_no·rfp_no 가 **둘 다 비어 있음**

즉 번호로 계획서와 완결보고서를 잇는 경로가 현재 데이터에는 사실상 없다. 그래서 번호가
없는 문서는 문서 자신을 과제로 대신 세운다(대체키). 중복성 분석은 당분간 **문서 대 문서**로
돈다.

**억지로 잇지 않는다.** 제목이 비슷하다고 묶으면 `merge.py`가 이름 유사도 병합을 거부하는
바로 그 이유(작물만 다른 과제가 조용히 하나가 된다)로 실패한다. 제목·파일명 기반 매칭은
근거를 갖춘 별도 과제로 남긴다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

import re

from .candidates import CandidateStore, DocumentMeta
from .identity import is_placeholder_title, strip_placeholder_prefix
from .merge import normalize_name

logger = logging.getLogger(__name__)

# 파일명 앞의 수집기관 일련번호. 과제명이 아니라 식별자라 제목에서 뗀다.
_DOCNAME_PREFIX = re.compile(r"^(?:TRKO|KAR)\d+[_\-\s]*", re.I)


def title_from_doc_name(doc_name: str) -> str:
    """파일명에서 과제명을 만든다 (E-93 폴백).

    추출된 제목이 서식 안내문(`(해당 시 작성)` 등)일 때 쓴다. 실측상 이 문서들의
    파일명은 **전부**(663/663) 진짜 과제명이었다:
        `TRKO202100010370_한우이유시기와단백질수준에따른대사생리및탄소저감연구.pdf`
        → `한우이유시기와단백질수준에따른대사생리및탄소저감연구`

    이 폴백이 **읽기 시점**에 있기 때문에 이미 저장된 문서 663건이 재추출 없이 고쳐진다.
    """
    name = (doc_name or "").strip()
    if not name:
        return ""
    name = re.sub(r"\.[^.]+$", "", name)  # 확장자
    name = _DOCNAME_PREFIX.sub("", name)
    name = re.sub(r"[_]+", " ", name).strip()
    return name


def _looks_like_title(text: str) -> bool:
    """제목다운가 — 목차 점선·반복 문자 같은 파싱 쓰레기를 거른다 (E-93).

    실측 사례: `(해당 시 작성) 1 ......................`, `(해당 시 작성) m m m m m m`.
    """
    t = (text or "").strip()
    if len(t) < 6:
        return False
    # 한글·영문 낱말이 실제로 들어 있어야 한다
    letters = re.findall(r"[가-힣A-Za-z]{2,}", t)
    if not letters:
        return False
    # 같은 한 글자가 반복되는 패턴(`m m m m`)이나 점선이 대부분이면 버린다
    meaningful = sum(len(w) for w in letters)
    return meaningful >= max(6, len(t) * 0.3)


SOURCE_PROJECT_NO = "PROJECT_NO"
SOURCE_DOC_SURROGATE = "DOCUMENT_SURROGATE"


@dataclass
class Project:
    """그래프에 올라갈 과제 하나."""

    project_id: str
    project_id_source: str
    title: str = ""
    normalized_title: str = ""
    year: int | None = None
    doc_ids: list[str] = field(default_factory=list)
    project_no: str = ""
    title_from_final_report: bool = False

    @property
    def document_count(self) -> int:
        return len(self.doc_ids)


def project_id_for(meta: DocumentMeta) -> tuple[str, str]:
    """문서 하나의 과제 id와 그 출처를 정한다."""
    no = (meta.project_no or "").strip()
    if no:
        return f"pj:{no}", SOURCE_PROJECT_NO
    return f"doc:{meta.doc_id}", SOURCE_DOC_SURROGATE


def resolve_projects(store: CandidateStore, persist: bool = True) -> list[Project]:
    """전체 문서를 과제로 묶는다. `persist=True`면 documents 표에 되짚어 기록한다."""
    docs = store.all_documents()
    grouped: dict[str, Project] = {}
    links: list[tuple[str, str, str]] = []

    for meta in docs:
        pid, source = project_id_for(meta)
        links.append((meta.doc_id, pid, source))
        proj = grouped.get(pid)
        if proj is None:
            proj = Project(
                project_id=pid,
                project_id_source=source,
                project_no=(meta.project_no or "").strip(),
            )
            grouped[pid] = proj
        proj.doc_ids.append(meta.doc_id)
        # 제목이 서식 안내문이면 파일명에서 만든다 (E-93). 추출을 다시 돌리지 않고
        # 이미 저장된 663건을 고치는 경로다.
        doc_title = (meta.title or "").strip()
        if is_placeholder_title(doc_title):
            doc_title = title_from_doc_name(meta.doc_name)
        else:
            # 안내문이 **앞에 붙은** 경우 그것만 떼면 뒤에 진짜 과제명이 남는다.
            # 떼고 남은 것이 알맹이가 아니면(목차 점선 등) 파일명으로 간다.
            stripped = strip_placeholder_prefix(doc_title)
            if stripped != doc_title:
                doc_title = (
                    stripped if _looks_like_title(stripped) else title_from_doc_name(meta.doc_name)
                )
        # 대표 제목은 완결보고서 것을 우선한다 — RFP 제목은 공고문 제목이라 과제명과 다를 수 있다.
        is_final = meta.document_type == "FINAL_REPORT"
        if doc_title and (not proj.title or (is_final and not proj.title_from_final_report)):
            proj.title = doc_title
            proj.title_from_final_report = is_final
        # 과제 시작연도로는 가장 이른 문서 연도를 쓴다 (RFP가 완결보고서보다 앞선다).
        if meta.year and (proj.year is None or meta.year < proj.year):
            proj.year = meta.year

    for proj in grouped.values():
        proj.normalized_title = normalize_name(proj.title)

    if persist:
        store.set_document_projects(links)

    by_source: dict[str, int] = defaultdict(int)
    for p in grouped.values():
        by_source[p.project_id_source] += 1
    multi = sum(1 for p in grouped.values() if p.document_count > 1)
    logger.info(
        "KG 과제 해석: 문서 %d → 과제 %d (번호기반 %d · 문서대체 %d, 다문서 과제 %d)",
        len(docs),
        len(grouped),
        by_source[SOURCE_PROJECT_NO],
        by_source[SOURCE_DOC_SURROGATE],
        multi,
    )
    return sorted(grouped.values(), key=lambda p: p.project_id)


__all__ = [
    "SOURCE_DOC_SURROGATE",
    "SOURCE_PROJECT_NO",
    "Project",
    "project_id_for",
    "resolve_projects",
]
