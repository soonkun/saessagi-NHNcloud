# src/document_ingest/segments.py
"""_Segment 내부 데이터 구조 + _chunk_segments 청킹 로직 (M_06 스펙 §6)."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from vector_search.types import DocumentChunk

logger = logging.getLogger(__name__)

# 문장 경계 분할 정규식:
# - 영어/범용: .!? 뒤 공백
# - 한국어 종결어미: 다. 요. 임. 죠. 네. 음. 됩니다. 습니다. 등 + 뒤 공백
# - 빈 줄(2개 이상 개행)
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?。！？])\s+"
    r"|(?<=다\.)\s+"
    r"|(?<=요\.)\s+"
    r"|(?<=임\.)\s+"
    r"|(?<=죠\.)\s+"
    r"|(?<=네\.)\s+"
    r"|\n{2,}"
)


@dataclass(frozen=True)
class _Segment:
    """파서가 반환하는 중간 단위. 청커의 입력이 된다.

    - 파서별로 다른 경계 개념을 통일: PDF=page, PPTX=slide, DOCX/MD=heading block,
      HWPX=section file, TXT=전체 1건.
    - text는 개행 포함 가능. 빈 문자열은 파서가 이미 걸러서 여기 도달하지 않는다.
    - page/section/bbox는 그대로 DocumentChunk에 전파.
    """

    text: str
    page: int | None
    section: str | None
    bbox: tuple[float, float, float, float] | None


def _split_sentences(text: str) -> list[str]:
    """텍스트를 문장 단위로 분할한다."""
    # 정규식으로 분할하되 빈 항목 제거
    parts = _SENTENCE_SPLIT_RE.split(text)
    sentences = [p.strip() for p in parts if p.strip()]
    return sentences


def _hard_split(text: str, chunk_chars: int) -> list[str]:
    """단일 문장이 chunk_chars보다 클 때 공백/음절 경계에서 강제 분할."""
    chunks: list[str] = []
    while len(text) > chunk_chars:
        # 공백에서 자르는 시도 (chunk_chars 이하의 마지막 공백)
        cut_pos = text.rfind(" ", 0, chunk_chars)
        if cut_pos <= 0:
            # 공백 없으면 chunk_chars 위치에서 강제 컷
            cut_pos = chunk_chars
        part = text[:cut_pos].strip()
        if part:
            chunks.append(part)
        text = text[cut_pos:].strip()
    if text:
        chunks.append(text)
    return chunks


def _chunk_segment(
    seg: _Segment,
    chunk_chars: int,
    overlap_chars: int,
    doc_id: str,
    doc_name: str,
    category: str | None,
    source_path: str,
) -> list[DocumentChunk]:
    """단일 _Segment를 DocumentChunk 리스트로 분할한다.

    스펙 §6.2 알고리즘 구현:
    1. 세그먼트 독립 청킹 (경계 넘지 않음)
    2. 문장 단위 누적
    3. chunk_chars 초과 전 청크 닫기
    4. overlap_chars 오버랩
    5. 단일 문장 > chunk_chars → 하드 컷
    6. 스트립 후 < 10자 drop
    """
    sentences = _split_sentences(seg.text)
    if not sentences:
        return []

    result: list[DocumentChunk] = []

    # 현재 누적 버퍼
    current_sentences: list[str] = []
    current_len: int = 0

    def flush_chunk(sentences_buf: list[str]) -> DocumentChunk | None:
        text = " ".join(sentences_buf).strip()
        if len(text) < 10:
            return None
        return DocumentChunk(
            doc_id=doc_id,
            doc_name=doc_name,
            category=category,
            page=seg.page,
            section=seg.section,
            chunk_id=str(uuid.uuid4()),
            text=text,
            bbox=seg.bbox,
            source_path=source_path,
        )

    for sentence in sentences:
        # 단일 문장이 chunk_chars 초과 → 하드 컷 후 개별 처리
        if len(sentence) > chunk_chars:
            # 먼저 현재 버퍼 flush
            if current_sentences:
                chunk = flush_chunk(current_sentences)
                if chunk:
                    result.append(chunk)
                current_sentences = []
                current_len = 0

            # 하드 컷 결과를 개별 청크로
            hard_parts = _hard_split(sentence, chunk_chars)
            for part in hard_parts:
                chunk = flush_chunk([part])
                if chunk:
                    result.append(chunk)
            continue

        # 추가했을 때 chunk_chars 초과 → 현재 버퍼 flush 후 새 버퍼 시작
        sep_len = 1 if current_sentences else 0  # 공백 구분자
        if current_len + sep_len + len(sentence) > chunk_chars and current_sentences:
            chunk = flush_chunk(current_sentences)
            if chunk:
                result.append(chunk)

            # 오버랩: 이전 청크의 마지막 overlap_chars 문자에 속하는 문장들을 찾아 시작점 조정
            if overlap_chars > 0:
                overlap_sentences: list[str] = []
                overlap_len = 0
                for prev_sentence in reversed(current_sentences):
                    candidate_len = len(prev_sentence) + (1 if overlap_sentences else 0)
                    if overlap_len + candidate_len <= overlap_chars:
                        overlap_sentences.insert(0, prev_sentence)
                        overlap_len += candidate_len
                    else:
                        break
                current_sentences = overlap_sentences
                current_len = overlap_len
            else:
                current_sentences = []
                current_len = 0

        sep_len = 1 if current_sentences else 0
        current_sentences.append(sentence)
        current_len += sep_len + len(sentence)

    # 남은 버퍼 flush
    if current_sentences:
        chunk = flush_chunk(current_sentences)
        if chunk:
            result.append(chunk)

    return result


# 개조식 머리기호 패턴 (불릿/번호/한글 항목). 병합 청킹에서 항목 경계 인식용.
_BULLET_RE = re.compile(
    r"^\s*(?:"
    r"[-*•·▪◦○●◯□■◻◼☐▶▷◆◇~]"  # 기호 불릿
    r"|[\(（]?[0-9０-９]+[.)）]"  # 1) 1. (1)
    r"|[\(（]?[①-⑳㉑-㉟]"  # 원숫자
    r"|[\(（]?[가-힣][.)）]"  # 가. 나) (다)
    r"|[ⓐ-ⓩ]|[a-zA-Z][.)]"  # a. b)
    r")\s*"
)


def _split_oversized(text: str, chunk_chars: int) -> list[str]:
    """단일 세그먼트가 chunk_chars를 초과할 때 문장→하드 분할로 잘게 나눈다."""
    out: list[str] = []
    cur: list[str] = []

    def join_cur() -> str:
        return " ".join(cur).strip()

    for sentence in _split_sentences(text):
        if len(sentence) > chunk_chars:
            if cur:
                out.append(join_cur())
                cur = []
            out.extend(_hard_split(sentence, chunk_chars))
            continue
        sep = 1 if cur else 0
        if cur and len(join_cur()) + sep + len(sentence) > chunk_chars:
            out.append(join_cur())
            cur = []
        cur.append(sentence)
    if cur:
        out.append(join_cur())
    return [o for o in (s.strip() for s in out) if o]


def chunk_meta_segments(
    meta_segments: list[tuple[str, int | None]],
    chunk_chars: int = 800,
    overlap_chars: int = 100,
    min_chunk_chars: int = 10,
) -> list[tuple[str, int | None]]:
    """(text, page) 메타 세그먼트들을 병합·청킹한다.

    개조식 문서는 파서가 단락(불릿 한 줄)마다 세그먼트를 1건씩 내보내므로,
    이를 그대로 청크로 쓰면 한 줄 = 한 청크가 되어 청크가 폭증한다(예: 3페이지 140청크).
    이 함수는 **같은 page에 속한 인접 세그먼트를 chunk_chars까지 누적 병합**해
    "한 소제목 아래 불릿 묶음"이 한 청크에 들어가도록 한다.

    - page가 바뀌면 병합하지 않는다 → 출처 페이지 메타 보존.
    - 줄 구조 유지를 위해 세그먼트는 "\n"으로 이어 붙인다(개조식 가독성).
    - 단일 세그먼트가 chunk_chars를 넘으면 문장/하드 분할로 잘게 나눈다.
    - overlap_chars > 0이면 직전 청크의 마지막 줄들을 overlap_chars 한도까지 다음 청크 앞에 재포함.
    - 병합 후에도 min_chunk_chars 미만인 청크는 버린다.

    Args:
        meta_segments: (텍스트, 페이지|None) 튜플 리스트. 파서 출력 그대로.
        chunk_chars:   청크 목표 크기(문자). 구조 문서는 300~500도 적합.
        overlap_chars: 청크 간 오버랩 크기.
        min_chunk_chars: 이보다 짧은 청크는 폐기.

    Returns:
        병합·청킹된 (텍스트, 페이지|None) 리스트. page는 입력 세그먼트의 값을 전파.
    """
    result: list[tuple[str, int | None]] = []
    buf: list[str] = []
    buf_page: int | None = None

    def joined(lines: list[str]) -> str:
        return "\n".join(lines).strip()

    def flush() -> None:
        nonlocal buf
        if buf:
            text = joined(buf)
            if len(text) >= min_chunk_chars:
                result.append((text, buf_page))
            buf = []

    for raw_text, seg_page in meta_segments:
        seg_text = (raw_text or "").strip()
        if not seg_text:
            continue

        # page 경계 → 병합 중단
        if buf and seg_page != buf_page:
            flush()

        # 단일 세그먼트가 너무 큼 → 독립 분할
        if len(seg_text) > chunk_chars:
            flush()
            for part in _split_oversized(seg_text, chunk_chars):
                if len(part) >= min_chunk_chars:
                    result.append((part, seg_page))
            continue

        # 추가 시 초과 → 현재 버퍼 flush 후 오버랩 재시드
        if buf and len(joined(buf + [seg_text])) > chunk_chars:
            prev = list(buf)
            flush()
            if overlap_chars > 0:
                overlap_lines: list[str] = []
                for line in reversed(prev):
                    if len(joined([line, *overlap_lines])) <= overlap_chars:
                        overlap_lines.insert(0, line)
                    else:
                        break
                buf = overlap_lines

        if not buf:
            buf_page = seg_page
        buf.append(seg_text)

    flush()
    return result


def chunk_segments(
    segments: list[_Segment],
    chunk_chars: int,
    overlap_chars: int,
    doc_id: str,
    doc_name: str,
    category: str | None,
    source_path: str,
) -> list[DocumentChunk]:
    """여러 _Segment를 독립적으로 청킹해 DocumentChunk 리스트를 반환한다.

    각 세그먼트는 독립 처리(경계를 넘지 않음, 스펙 §6.2.1).
    """
    result: list[DocumentChunk] = []
    for seg in segments:
        chunks = _chunk_segment(
            seg=seg,
            chunk_chars=chunk_chars,
            overlap_chars=overlap_chars,
            doc_id=doc_id,
            doc_name=doc_name,
            category=category,
            source_path=source_path,
        )
        result.extend(chunks)
    return result
