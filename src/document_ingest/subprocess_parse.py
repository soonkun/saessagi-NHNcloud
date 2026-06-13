# src/document_ingest/subprocess_parse.py
"""격리 파싱 워커 — 별도 프로세스에서 실행되는 문서 파서 진입점.

네이티브 파서(pypdfium2 등)가 손상/비호환 PDF 페이지에서 illegal-instruction
(WinError 0xc000001d 등)으로 프로세스를 통째로 죽이는 사고가 있었다(E-48).
파싱을 이 모듈의 함수로 분리해 ProcessPoolExecutor 워커에서 호출하면, 크래시가
나도 자식 프로세스만 종료되고 백엔드 메인 프로세스는 살아남는다.

이 모듈은 spawn 시 자식이 import하므로 **무거운 의존성(torch/fastapi 등)을 모듈
레벨에서 import하지 않는다** — 파서는 함수 안에서 지연 import한다.
"""

from __future__ import annotations

import os
import tempfile


def parse_to_meta_segments(filename: str, data: bytes) -> list[tuple[str, int | None]]:
    """파서를 호출해 (text, page) 튜플 목록을 반환한다.

    rag_routes._parse_to_meta_segments와 동작이 100% 동일해야 한다(이 함수가 정본).

    - PDF/PPTX : page = 실제 페이지/슬라이드 번호(1-based).
    - HWPX/DOCX/TXT/MD : page = None.
    - 알 수 없는 확장자 : 전체 텍스트를 page=None 1건으로 반환.
    """
    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        from document_ingest.parsers.docx import _parse_docx
        from document_ingest.parsers.hwpx import _parse_hwpx
        from document_ingest.parsers.md import _parse_md
        from document_ingest.parsers.pdf import _parse_pdf
        from document_ingest.parsers.pptx import _parse_pptx
        from document_ingest.parsers.txt import _parse_txt

        parser_map = {
            ".pdf": _parse_pdf,
            ".docx": _parse_docx,
            ".pptx": _parse_pptx,
            ".hwpx": _parse_hwpx,
            ".txt": _parse_txt,
            ".md": _parse_md,
            ".markdown": _parse_md,
        }

        parser = parser_map.get(suffix)
        if parser is None:
            raw = data.decode("utf-8", errors="replace").strip()
            return [(raw, None)] if raw else []

        return [(seg.text.strip(), seg.page) for seg in parser(tmp_path) if seg.text.strip()]
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
