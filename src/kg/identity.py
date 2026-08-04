# src/kg/identity.py
"""M_23 과제 식별 정보 추출 — 과제번호·RFP번호·과제명 (지침서 2장, 스펙 §3 (1)).

**LLM을 쓰지 않는다.** 지침서 7.2가 "기관과 사람은 가능한 경우 표지·정형 필드 파싱으로
추출한다. 청크 LLM 추출에 의존하지 않는다"고 한 것과 같은 이유다. 과제번호는 `PJ013094`
같은 정형 코드라 정규식이 LLM보다 정확하고, 문서당 호출을 하나 아낀다.

이 값이 계획서와 완결보고서를 **같은 Project로 묶는 축**이다(지침서 2장). 여기서 틀리면
"계획 대비 실적" 비교가 통째로 무너지므로, 확신이 없으면 빈 값을 남긴다 — 잘못된 번호로
서로 다른 과제를 합치는 것보다 낫다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 농촌진흥청 계열 과제번호: PJ + 6자리 이상. 세부과제는 뒤에 두 자리가 더 붙기도 한다.
_PROJECT_NO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:세부)?과제\s*(?:고유)?번호[\s:：]*([A-Z]{2}\s?\d{6,}(?:-?\d{2,})?)", re.I),
    re.compile(r"\b(PJ\s?\d{6,}(?:-?\d{2,})?)\b", re.I),
)

_RFP_NO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"RFP\s*(?:번호|No\.?)[\s:：]*([A-Za-z0-9\-]{4,})", re.I),
    re.compile(r"\b(RFP[-\s]?\d{4}[-\s]?\d{2,})\b", re.I),
)

# 과제명은 "과제명: ..." 형태가 가장 믿을 만하다. 표지의 큰 제목은 문서명으로 대체 가능.
_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:연구)?과제\s*명[\s:：]*(.+)"),
    re.compile(r"사업\s*명[\s:：]*(.+)"),
)

_YEAR_RANGE = re.compile(r"(19|20)(\d{2})\s*[~\-–—]\s*(19|20)?(\d{2})")

# 제목으로 받아들이지 않을 것 — 표지의 안내 문구가 걸리는 것을 막는다.
_TITLE_MIN = 6
_TITLE_MAX = 200


@dataclass(frozen=True)
class ProjectIdentity:
    """문서에서 읽어낸 과제 식별 정보. 못 찾으면 빈 값."""

    project_no: str = ""
    rfp_no: str = ""
    title: str = ""
    start_year: int | None = None
    end_year: int | None = None


def _clean_code(code: str) -> str:
    return re.sub(r"\s+", "", code or "").upper()


def _clean_title(raw: str) -> str:
    title = re.sub(r"\s+", " ", raw or "").strip()
    # 뒤에 붙은 표·괄호 안내를 자른다: "○○ 개발 (1차년도)" → "○○ 개발"
    title = re.sub(r"\s*[\(（]\s*\d+\s*차\s*년?도?\s*[\)）]\s*$", "", title)
    title = title.strip(" .·:：-–—\t")
    return title


def extract_identity(text: str) -> ProjectIdentity:
    """문서 앞부분 텍스트에서 과제 식별 정보를 뽑는다."""
    body = text or ""

    project_no = ""
    for pattern in _PROJECT_NO_PATTERNS:
        m = pattern.search(body)
        if m:
            project_no = _clean_code(m.group(1))
            break

    rfp_no = ""
    for pattern in _RFP_NO_PATTERNS:
        m = pattern.search(body)
        if m:
            rfp_no = _clean_code(m.group(1))
            break

    title = ""
    for pattern in _TITLE_PATTERNS:
        m = pattern.search(body)
        if m:
            candidate = _clean_title(m.group(1))
            if _TITLE_MIN <= len(candidate) <= _TITLE_MAX:
                title = candidate
                break

    start_year: int | None = None
    end_year: int | None = None
    ym = _YEAR_RANGE.search(body)
    if ym:
        start_year = int(ym.group(1) + ym.group(2))
        century = ym.group(3) or ym.group(1)
        end_year = int(century + ym.group(4))
        if end_year < start_year:
            start_year, end_year = None, None

    return ProjectIdentity(
        project_no=project_no,
        rfp_no=rfp_no,
        title=title,
        start_year=start_year,
        end_year=end_year,
    )


def project_key(identity: ProjectIdentity, doc_id: str) -> str:
    """Project 노드 키.

    과제번호가 있으면 그것을 쓴다 — 계획서와 완결보고서가 같은 노드로 묶이는 지점이다.
    없으면 문서별로 따로 둔다. **제목 유사도로 묶지 않는다**: 과제명이 비슷해도 작물이
    다르면 다른 과제라는 것이 이 시스템의 대전제다(지침서 3장).
    """
    if identity.project_no:
        return f"project:{identity.project_no}"
    if identity.rfp_no:
        return f"rfp:{identity.rfp_no}"
    return f"doc:{doc_id}"
