"""Frontmatter + 위키링크 파싱.

외부 의존성을 최소화하기 위해 yaml만 사용 (PyYAML은 upstream 의존성).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
# [[slug]] 또는 [[doc:doc_id]] — 첫 토큰만 추출
_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:\|[^\]]*)?\]\]")


@dataclass
class ParsedNote:
    frontmatter: dict[str, object]
    body: str


def parse(text: str) -> ParsedNote:
    """frontmatter block + 본문 분리. frontmatter 없으면 빈 dict."""
    m = _FM_RE.match(text)
    if m is None:
        return ParsedNote(frontmatter={}, body=text.strip())
    fm_raw = m.group(1)
    body = m.group(2).strip()
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError:
        # 깨진 frontmatter는 빈 dict로 처리 — 호출자가 skip 결정
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return ParsedNote(frontmatter=fm, body=body)


def serialize(frontmatter: dict[str, object], body: str) -> str:
    """frontmatter + 본문 합성. yaml.dump 결과를 그대로 사용."""
    fm_yaml = yaml.dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()
    return f"---\n{fm_yaml}\n---\n\n{body.strip()}\n"


def extract_wikilinks(body: str) -> list[str]:
    """본문에서 [[slug]] 또는 [[doc:xxx]] 위키링크 추출.

    `doc:` 접두사가 붙은 것은 도큐먼트 참조이므로 제외하고 노트 슬러그만 반환.
    """
    out: list[str] = []
    for m in _WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        if target.startswith("doc:"):
            continue
        if target and target not in out:
            out.append(target)
    return out
