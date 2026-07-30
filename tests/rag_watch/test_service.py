"""M_22 서비스 실행 경로 테스트 (CR-41) — 삭제 정책·중복 방지·스캔 겹침."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from rag_watch import RagWatchService
from rag_watch.state import file_digest


class _FakeStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.recategorized: list[tuple[str, str | None]] = []

    def delete_by_doc_id(self, doc_id: str) -> int:
        self.deleted.append(doc_id)
        return 1

    def update_doc_category(self, doc_id: str, category: str | None) -> int:
        self.recategorized.append((doc_id, category))
        return 1


class _FakeRag:
    def __init__(self, store: _FakeStore) -> None:
        self._store = store


class _FakeCtx:
    def __init__(self, store: _FakeStore) -> None:
        self.rag_service = _FakeRag(store)
        self.app_config = None


@pytest.fixture()
def env(tmp_path: Path):
    root = tmp_path / "RAG"
    root.mkdir()
    store = _FakeStore()
    svc = RagWatchService(
        root=root,
        state_path=tmp_path / "state.json",
        service_context=_FakeCtx(store),
        max_per_cycle=10,
    )
    return root, store, svc


def write(p: Path, text: str = "내용") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ────────────────────────────────────────────────────────────
# 인제스트 호출
# ────────────────────────────────────────────────────────────


class TestIngest:
    @pytest.mark.asyncio
    async def test_i1_ingests_once_not_twice(self, env, monkeypatch: pytest.MonkeyPatch) -> None:
        """같은 파일을 매 주기 다시 임베딩하면 색인이 중복으로 불어난다."""
        root, _store, svc = env
        write(root / "f" / "a.md")

        calls: list[str] = []

        class _Res:
            doc_id = "doc-1"
            chunk_count = 3

        async def fake_ingest(_ctx: Any, *, filename: str, data: bytes, folder_id: Any) -> Any:
            calls.append(filename)
            return _Res()

        import app.rag_routes as rr

        monkeypatch.setattr(rr, "ingest_document_bytes", fake_ingest)
        monkeypatch.setattr(rr, "_load_folders", lambda: [{"folder_id": "fid", "name": "f"}])

        await svc.run_once()  # 1회차: 안정화 대기
        assert calls == []

        await svc.run_once()  # 2회차: 인제스트
        assert calls == ["a.md"]

        await svc.run_once()  # 3회차: 이미 등록 → 재인제스트 없음
        assert calls == ["a.md"]

    @pytest.mark.asyncio
    async def test_i2_failure_retried_next_cycle(self, env, monkeypatch: pytest.MonkeyPatch) -> None:
        """실패를 상태에 기록하면 영구히 누락된다 — 다음 주기에 재시도해야 한다."""
        root, _store, svc = env
        write(root / "f" / "a.md")

        attempts: list[int] = []

        async def flaky(_ctx: Any, **_kw: Any) -> Any:
            attempts.append(1)
            raise RuntimeError("일시 실패")

        import app.rag_routes as rr

        monkeypatch.setattr(rr, "ingest_document_bytes", flaky)
        monkeypatch.setattr(rr, "_load_folders", lambda: [{"folder_id": "fid", "name": "f"}])

        await svc.run_once()
        await svc.run_once()
        await svc.run_once()
        assert len(attempts) == 2, "실패한 파일은 계속 재시도되어야 한다"


# ────────────────────────────────────────────────────────────
# 삭제 정책
# ────────────────────────────────────────────────────────────


class TestDeletePolicy:
    @pytest.mark.asyncio
    async def test_d1_ignore_keeps_index(self, env) -> None:
        """기본값. 마운트 실패로 문서 수백 건이 조용히 사라지는 것을 막는다."""
        _root, store, svc = env
        svc.state.record("h1", rel_path="f/gone.md", doc_id="d1", folder_name="f", size=1)

        await svc.run_once()
        assert store.deleted == []
        assert svc.state.get("h1") is not None

    @pytest.mark.asyncio
    async def test_d2_unindex_removes(self, tmp_path: Path) -> None:
        root = tmp_path / "RAG"
        root.mkdir()
        store = _FakeStore()
        svc = RagWatchService(
            root=root,
            state_path=tmp_path / "s.json",
            service_context=_FakeCtx(store),
            delete_policy="unindex",
        )
        svc.state.record("h1", rel_path="f/gone.md", doc_id="d1", folder_name="f", size=1)

        await svc.run_once()
        assert store.deleted == ["d1"]
        assert svc.state.get("h1") is None


# ────────────────────────────────────────────────────────────
# 폴더 이동
# ────────────────────────────────────────────────────────────


class TestMove:
    @pytest.mark.asyncio
    async def test_m1_move_updates_category_without_reembedding(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root, store, svc = env
        p = write(root / "새폴더" / "a.md")
        svc.state.record(
            file_digest(p), rel_path="옛폴더/a.md", doc_id="d1", folder_name="옛폴더", size=6
        )

        ingested: list[str] = []

        async def fake_ingest(_ctx: Any, **kw: Any) -> Any:
            ingested.append(kw["filename"])
            raise AssertionError("이동인데 재임베딩이 일어났다")

        import app.rag_routes as rr

        monkeypatch.setattr(rr, "ingest_document_bytes", fake_ingest)
        monkeypatch.setattr(
            rr, "_load_folders", lambda: [{"folder_id": "new", "name": "새폴더"}]
        )

        await svc.run_once()
        await svc.run_once()

        assert ingested == []
        assert store.recategorized == [("d1", "new")]
        assert (svc.state.get(file_digest(p)) or {}).get("folder_name") == "새폴더"


# ────────────────────────────────────────────────────────────
# 겹침 방지
# ────────────────────────────────────────────────────────────


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_x1_overlapping_scan_skipped(self, env, monkeypatch: pytest.MonkeyPatch) -> None:
        """인제스트가 주기보다 길면 스캔이 겹쳐 같은 파일을 두 번 넣는다."""
        root, _store, svc = env
        write(root / "f" / "a.md")

        import app.rag_routes as rr

        monkeypatch.setattr(rr, "_load_folders", lambda: [{"folder_id": "fid", "name": "f"}])

        started = asyncio.Event()

        class _Res:
            doc_id = "d1"
            chunk_count = 1

        async def slow(_ctx: Any, **_kw: Any) -> Any:
            started.set()
            await asyncio.sleep(0.2)
            return _Res()

        monkeypatch.setattr(rr, "ingest_document_bytes", slow)

        await svc.run_once()  # 안정화
        task = asyncio.create_task(svc.run_once())
        await started.wait()

        overlapped = await svc.run_once()  # 진행 중 → 건너뛰어야 한다
        assert overlapped.is_empty()
        await task


# ────────────────────────────────────────────────────────────
# 상태 파일
# ────────────────────────────────────────────────────────────


class TestState:
    @pytest.mark.asyncio
    async def test_t1_corrupt_state_does_not_crash(self, tmp_path: Path) -> None:
        """상태가 깨졌다고 기능이 멈추면 안 된다 — 최악의 경우 재인제스트일 뿐이다."""
        sp = tmp_path / "s.json"
        sp.write_text("{{{ 깨진 json", encoding="utf-8")
        root = tmp_path / "RAG"
        root.mkdir()

        svc = RagWatchService(
            root=root, state_path=sp, service_context=_FakeCtx(_FakeStore())
        )
        assert len(svc.state) == 0
        await svc.run_once()

    @pytest.mark.asyncio
    async def test_t2_missing_root_is_quiet(self, tmp_path: Path) -> None:
        svc = RagWatchService(
            root=tmp_path / "없음",
            state_path=tmp_path / "s.json",
            service_context=_FakeCtx(_FakeStore()),
        )
        assert (await svc.run_once()).is_empty()
