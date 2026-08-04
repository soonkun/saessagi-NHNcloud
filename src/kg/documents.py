# src/kg/documents.py
"""M_23 문서 메타 유도 + 추출 대상 청크 선별 (스펙 §3 (1)(2)).

지침서 5.1은 문서 메타데이터를 모든 청크에 전파하라고 하지만, 그러려면 LanceDB 스키마를
바꾸고 325,570건을 다시 써야 한다("기존 임베딩 파이프라인을 손상하지 않는다"와 충돌).
대신 **문서 단위로 한 번만** 확정해 후보 저장소에 둔다. 추출할 때 프롬프트에 실어 주면
효과는 같다.

문서유형·연도는 **폴더 이름에서 유도한다.** 실측 결과 category(=folder_id)가
`2025완결보고서`, `RFP(2020-2025)` 같은 폴더로 이어지므로 LLM에 물어볼 필요가 없다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 문서 유형 (지침서 6.1 Document.document_type)
DOC_TYPE_FINAL_REPORT = "FINAL_REPORT"
DOC_TYPE_RFP = "RFP"
DOC_TYPE_PLAN = "PLAN"
DOC_TYPE_ANNUAL = "ANNUAL_REPORT"
DOC_TYPE_REFERENCE = "REFERENCE"
DOC_TYPE_UNKNOWN = "UNKNOWN"

# 폴더 이름 → 문서 유형. 긴 것부터 본다.
_FOLDER_TYPE_PATTERNS: list[tuple[str, str]] = [
    ("완결보고서", DOC_TYPE_FINAL_REPORT),
    ("최종보고서", DOC_TYPE_FINAL_REPORT),
    ("연차보고서", DOC_TYPE_ANNUAL),
    ("계획서", DOC_TYPE_PLAN),
    ("제안요청서", DOC_TYPE_RFP),
    ("RFP", DOC_TYPE_RFP),
]

_YEAR_RE = re.compile(r"(19|20)\d{2}")


@dataclass(frozen=True)
class FolderInfo:
    """폴더에서 읽어낸 문서 맥락."""

    folder_id: str
    folder_name: str
    document_type: str
    year: int | None
    year_end: int | None = None


def classify_folder(folder_id: str, folder_name: str) -> FolderInfo:
    """폴더 이름에서 문서 유형과 연도를 읽는다.

    예)
      "2025완결보고서"     → FINAL_REPORT, 2025
      "RFP(2020-2025)"     → RFP, 2020~2025
      "참고자료"           → UNKNOWN, None
    """
    name = (folder_name or "").strip()
    doc_type = DOC_TYPE_UNKNOWN
    for token, mapped in _FOLDER_TYPE_PATTERNS:
        if token.lower() in name.lower():
            doc_type = mapped
            break

    years = [int(m.group()) for m in _YEAR_RE.finditer(name)]
    year = years[0] if years else None
    year_end = years[-1] if len(years) > 1 else None
    return FolderInfo(
        folder_id=folder_id,
        folder_name=name,
        document_type=doc_type,
        year=year,
        year_end=year_end,
    )


def build_folder_index(folders: list[dict[str, Any]]) -> dict[str, FolderInfo]:
    """`data/rag_folders.json` 목록 → folder_id 색인."""
    index: dict[str, FolderInfo] = {}
    for f in folders or []:
        fid = str(f.get("folder_id") or "")
        if not fid:
            continue
        index[fid] = classify_folder(fid, str(f.get("name") or ""))
    return index


# ── 추출 대상 청크 선별 ───────────────────────────────────────────────────────

# 연구의 실체(목표·내용·결과)가 적힌 자리를 가리키는 표지.
# 완결보고서·RFP 모두 이런 제목 아래에 핵심이 모여 있다.
_HIGH_VALUE_PATTERNS: tuple[tuple[str, float], ...] = (
    ("연구개발의 목표", 3.0),
    ("연구개발 목표", 3.0),
    ("최종목표", 3.0),
    ("연구목표", 3.0),
    ("연구개발의 내용", 2.5),
    ("연구개발 내용", 2.5),
    ("연구내용", 2.5),
    ("연구수행 내용", 2.5),
    ("연구개발 결과", 2.5),
    ("연구결과", 2.5),
    ("주요 연구성과", 2.5),
    ("연구성과", 2.0),
    ("기대효과", 1.5),
    ("활용방안", 1.5),
    ("추진전략", 1.5),
    ("개발 목표", 2.0),
    ("성과목표", 2.0),
    ("과제명", 2.0),
    ("사업명", 1.5),
    ("연구범위", 2.0),
    ("필요성", 1.2),
)

# 추출해도 건질 것이 없는 자리 — 지침서 8.2의 ADMINISTRATIVE/IRRELEVANT에 해당한다.
_LOW_VALUE_PATTERNS: tuple[str, ...] = (
    "참고문헌",
    "인용문헌",
    "References",
    "목    차",
    "목차",
    "제출문",
    "붙임",
    "별첨",
    "서식",
    "연구비",
    "소요예산",
    "집행실적",
    "정산",
    "개인정보",
    "보안등급",
)


# 청크 본문 맨 앞에 우리가 붙여 둔 인용 머리말. 실측 결과 LLM이 이것을 **근거 문장으로
# 인용**하는 일이 있었다(근거가 원문 내용이 아니라 파일명이 되는 셈). 추출에 보내기 전에
# 걷어낸다. 저장된 청크는 건드리지 않는다 — 인용 표시는 검색 결과 표시에 쓰인다.
_SOURCE_MARKER = re.compile(r"^\s*\[출처:[^\]]*\]\s*")


def strip_source_marker(text: str) -> str:
    """추출·근거 대조에 쓸 본문에서 인용 머리말을 뗀다."""
    return _SOURCE_MARKER.sub("", text or "")


@dataclass(frozen=True)
class ScoredChunk:
    """선별 점수가 매겨진 청크."""

    chunk_id: str
    doc_id: str
    page: int | None
    text: str
    score: float
    section_hint: str


def _section_hint(text: str) -> str:
    """청크 앞부분에서 소제목처럼 보이는 줄을 찾는다.

    Section 컬럼이 전부 비어 있어(현황조사 §2.1) 계층을 만들 수 없으므로, 최소한
    **어느 대목에서 나온 근거인지**를 Mention 속성으로 남기기 위한 힌트다. 나중에 섹션
    계층이 필요해지면 재파싱 없이 이 값으로 보강할 수 있다.
    """
    for raw in (text or "").splitlines()[:6]:
        line = raw.strip()
        if not line or len(line) > 60:
            continue
        for token, _ in _HIGH_VALUE_PATTERNS:
            if token in line:
                return line[:60]
        # "제3장 연구수행 내용", "3. 연구결과" 같은 번호 붙은 제목
        if re.match(r"^(제?\s*\d+\s*[장절.)]|[가-힣]\s*[.)])\s*\S", line):
            return line[:60]
    return ""


# 하드 제외 표시. 길이 감점 같은 "낮은 점수"와 **반드시 구분해야 한다** —
# 둘을 같은 값으로 두면 표지처럼 짧지만 꼭 필요한 청크가 참고문헌과 함께 버려진다
# (테스트 test_always_includes_document_head가 실제로 이걸 잡았다).
EXCLUDED = float("-inf")


def score_chunk(text: str) -> tuple[float, str]:
    """청크가 추출 가치가 있는지 점수화한다. 반환 (점수, 소제목 힌트).

    LLM 호출을 문서당 몇 개로 줄여야 하므로(전 청크는 900시간) 어디를 볼지 골라야 한다.
    규칙 기반으로 먼저 거른 뒤 상위만 LLM에 보낸다.

    점수 `EXCLUDED`는 "무슨 일이 있어도 보내지 않는다"는 뜻이고, 그 외의 낮은 점수는
    단지 우선순위가 낮다는 뜻이다.
    """
    body = text or ""
    if not body.strip():
        return EXCLUDED, ""

    lowered = body.lower()
    for bad in _LOW_VALUE_PATTERNS:
        if bad.lower() in lowered[:200]:
            return EXCLUDED, ""

    score = 0.0
    for token, weight in _HIGH_VALUE_PATTERNS:
        if token in body:
            score += weight

    # 너무 짧은 청크는 표지·목차 조각일 확률이 높다.
    length = len(body.strip())
    if length < 120:
        score -= 1.0
    elif length > 400:
        score += 0.5

    # 수치·단위가 있으면 실적·결과일 가능성이 높다 (지침서 8.1 RESULT 판별과 같은 취지).
    if re.search(r"\d+\s*(%|kg|ha|톤|건|개|년|배|점|㎏|㏊)", body):
        score += 0.5

    return score, _section_hint(body)


def select_chunks(rows: list[dict[str, Any]], budget: int) -> list[ScoredChunk]:
    """문서의 청크 중 추출할 것을 고른다.

    - 규칙 점수 상위 `budget`개를 고른다.
    - **문서 앞부분(1~3페이지)은 점수와 무관하게 최소 1개 포함한다.** 과제명·과제번호가
      거기 있어서, 이걸 놓치면 계획서와 완결보고서를 같은 Project로 묶지 못한다.
    - 동점이면 페이지 순서를 지켜 근거가 문서 전반에 퍼지게 한다.
    """
    if budget <= 0 or not rows:
        return []

    scored: list[ScoredChunk] = []
    for r in rows:
        text = strip_source_marker(str(r.get("text") or ""))
        score, hint = score_chunk(text)
        page_raw = r.get("page")
        try:
            page = int(page_raw) if page_raw is not None else None
        except (TypeError, ValueError):
            page = None
        scored.append(
            ScoredChunk(
                chunk_id=str(r.get("chunk_id") or ""),
                doc_id=str(r.get("doc_id") or ""),
                page=page,
                text=text,
                score=score,
                section_hint=hint,
            )
        )

    # 첫머리 확보 — 과제명·과제번호가 있는 자리.
    # 표지는 짧아서 점수가 낮지만, 여기를 놓치면 계획서와 완결보고서를 같은 Project로
    # 묶지 못한다. 그래서 점수가 아니라 **하드 제외 여부만** 본다.
    head = [c for c in scored if c.page is not None and c.page <= 3 and c.score > EXCLUDED]
    head.sort(key=lambda c: (c.page or 0, -c.score))
    picked: list[ScoredChunk] = head[:1]
    picked_ids = {c.chunk_id for c in picked}

    rest = [c for c in scored if c.chunk_id not in picked_ids and c.score > 0]
    rest.sort(key=lambda c: (-c.score, c.page or 0, c.chunk_id))
    picked.extend(rest[: max(0, budget - len(picked))])

    # 그래도 예산이 남고 쓸 만한 청크가 있으면 채운다(점수 0이어도 본문일 수 있다).
    if len(picked) < budget:
        picked_ids = {c.chunk_id for c in picked}
        filler = [c for c in scored if c.chunk_id not in picked_ids and c.score > EXCLUDED]
        filler.sort(key=lambda c: (-c.score, c.page or 0, c.chunk_id))
        picked.extend(filler[: budget - len(picked)])

    picked.sort(key=lambda c: (c.page or 0, c.chunk_id))
    return picked[:budget]
