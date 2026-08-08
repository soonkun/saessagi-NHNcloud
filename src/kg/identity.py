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

# 서식 안내문·양식 라벨 (E-93).
#
# `과제명: (해당 시 작성)` 처럼 **채우라고 비워 둔 칸**을 과제명으로 잡아 왔다.
# 실측 663건이 제목 `(해당 시 작성)`, 서식 문구 제목 합계 686/11,276(6.1%).
# 그래프 화면에 `(해당 시 작성)` 노드가 여럿 떠서 사용자가 "저건 뭐냐"고 물었다.
#
# 여기 넣는 것은 **도메인 지식이 아니라 서식 어휘**다 — 작물명 같은 내용어를 넣지 않는다.
# 목록에서 빠뜨려도 안전한 쪽으로 실패한다(제목이 그냥 남을 뿐).
_PLACEHOLDER_EXACT: frozenset[str] = frozenset(
    {
        "해당 시 작성",
        "해당시 작성",
        "해당없음",
        "해당 없음",
        "내역사업명",
        "단위사업명",
        "사업명",
        "과제명",
        "세부과제명",
        "연구과제명",
        "자유 제안 과제",
        "미정",
        "없음",
    }
)

# 제목 전체가 이 라벨로만 이루어졌으면 값이 아니라 양식 자체다.
_LABEL_ONLY = re.compile(
    r"^[\s(（\[]*(?:단위|내역|세부)?\s*(?:사업|과제|연구과제)\s*명[\s:：)）\]]*$"
)


def strip_placeholder_prefix(title: str) -> str:
    """제목 앞에 붙은 서식 안내문을 떼어 낸다 (E-93).

    표지에서 두 칸이 한 줄로 붙는 경우가 있다:
        `총괄 연구개발과제명 (해당 시 작성) 연구개발과제명 지열히트펌프를 이용한…`
    → 정규식이 둘 다 잡아 `(해당 시 작성) 지열히트펌프를 이용한…`이 된다.
    앞의 안내문만 떼면 **뒤에 진짜 과제명이 그대로 남는다.**

    **안내문 없이 라벨만 앞에 붙는 경우도 뗀다 (E-99).** E-93에서는 `해당 시 작성`이
    앞에 있는 형태만 봤는데, 실측하니 라벨 단독 접두가 훨씬 많았다:
        주관과제명 2,595 · 단위사업명 154 · 내역사업명 73 · 연구개발과제명 18
        → 제목 12,070건의 23.6%
    `주관과제명 무잔량 곡물건조기 개발` 같은 것이 그래프에 그대로 노드 이름으로 떴다.

    떼고 남은 것이 알맹이가 아니면(목차 점선 등) 호출자가 판정해 파일명으로 넘어간다.
    """
    t = re.sub(r"\s+", " ", title or "").strip()
    # 라벨 한 조각 — `주관과제명`·`단위사업명`·`연구개발과제명`·`사업명` 등.
    # 여기 넣는 것은 **서식 어휘**지 내용어가 아니다. 빠뜨려도 안전한 쪽으로 실패한다.
    label = r"(?:총괄|주관|세부|위탁|협동|공동|단위|내역)?\s*(?:연구개발)?\s*(?:과제|사업)\s*명"
    for _ in range(3):  # `(해당 시 작성) 연구개발과제명 …` 처럼 두 겹인 경우
        m = re.match(
            # (a) 안내문 [+ 뒤따르는 라벨]  또는  (b) 라벨 단독
            rf"^[\s(（\[]*(?:해당\s*시\s*작성|내역사업명|단위사업명|해당\s*없음)[\s)）\]]*"
            rf"(?:{label}[\s:：]*)?"
            rf"|^[\s(（\[]*{label}[\s)）\]]*[\s:：]*",
            t,
        )
        if not m or not m.group(0).strip():
            break
        rest = t[m.end() :].strip()
        # 떼고 나면 아무것도 안 남는 경우(제목이 라벨뿐)는 원본을 지킨다 —
        # 그건 `is_placeholder_title`이 판정할 몫이지 여기서 빈 문자열로 만들 일이 아니다.
        if not rest:
            break
        t = rest
    return t


def is_placeholder_title(title: str) -> bool:
    """제목이 실제 과제명이 아니라 서식 안내문인지 (E-93).

    `identity`(추출 시점)와 `projects`(표시 시점)가 **같은 판정을 써야 한다.**
    목록을 두 곳에 복사하면 한쪽만 고쳐지는 사고가 난다.
    """
    t = re.sub(r"\s+", " ", title or "").strip()
    if not t:
        return True
    # 괄호를 벗겨 본다: "(해당 시 작성)" → "해당 시 작성"
    bare = t.strip("()（）[]{}<>· \t").strip()
    if bare in _PLACEHOLDER_EXACT or t in _PLACEHOLDER_EXACT:
        return True
    if _LABEL_ONLY.match(t):
        return True
    # 괄호만 벗기면 안내문인 경우: "(내역사업명)" 등
    if bare and bare in _PLACEHOLDER_EXACT:
        return True
    return False


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
        # 패턴마다 첫 매치만 보면 `과제명: (해당 시 작성)`에서 멈춘다. 같은 문서 뒤쪽에
        # 진짜 과제명이 또 나오는 경우가 있어 안내문이면 다음 매치를 계속 본다 (E-93).
        for m in pattern.finditer(body):
            candidate = _clean_title(m.group(1))
            if not (_TITLE_MIN <= len(candidate) <= _TITLE_MAX):
                continue
            if is_placeholder_title(candidate):
                continue
            title = candidate
            break
        if title:
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
