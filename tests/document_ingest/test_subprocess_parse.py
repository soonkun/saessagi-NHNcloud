"""E-48 회귀 테스트: 격리 파싱 모듈 + 워커 크래시 격리.

- 파싱 정본 로직(subprocess_parse.parse_to_meta_segments)이 기대대로 동작하는지.
- 별도 프로세스(spawn) 워커가 급사해도 BrokenProcessPool로 표면화되어
  부모 프로세스(=백엔드)는 죽지 않는지 (PDF 네이티브 크래시 보호의 핵심).
"""

import asyncio
import concurrent.futures
import multiprocessing as mp
import os
from concurrent.futures.process import BrokenProcessPool

import pytest

from document_ingest.subprocess_parse import parse_to_meta_segments


def _crash_worker(*_args: object) -> None:
    """네이티브 크래시를 모사 — 프로세스를 급사시킨다."""
    os._exit(70)


def test_parse_txt_returns_text_with_no_page() -> None:
    segs = parse_to_meta_segments("note.txt", "첫째 줄\n둘째 줄".encode("utf-8"))
    assert len(segs) >= 1
    assert all(page is None for _, page in segs)
    assert "첫째 줄" in segs[0][0]


def test_parse_unknown_extension_returns_raw_text() -> None:
    segs = parse_to_meta_segments("data.xyz", b"plain content")
    assert segs == [("plain content", None)]


def test_parse_empty_unknown_returns_empty() -> None:
    assert parse_to_meta_segments("data.xyz", b"   ") == []


def test_subprocess_normal_parse() -> None:
    """spawn 워커에서 정상 파싱이 동작한다(격리 경로 sanity)."""

    async def _go() -> list[tuple[str, int | None]]:
        loop = asyncio.get_running_loop()
        ctx = mp.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as ex:
            return await loop.run_in_executor(ex, parse_to_meta_segments, "n.txt", b"hello world")

    assert asyncio.run(_go()) == [("hello world", None)]


def test_worker_crash_is_contained() -> None:
    """워커 급사 → BrokenProcessPool. 부모는 살아남아 그 다음 코드를 실행한다 (E-48)."""

    async def _go() -> None:
        loop = asyncio.get_running_loop()
        ctx = mp.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=ctx) as ex:
            with pytest.raises(BrokenProcessPool):
                await loop.run_in_executor(ex, _crash_worker)

    asyncio.run(_go())
    # 여기 도달했다는 것 자체가 부모 프로세스 생존의 증거.
    assert True
