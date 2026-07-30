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

    @staticmethod
    def _unindex_svc(tmp_path: Path, store: _FakeStore, **kw: Any) -> RagWatchService:
        root = tmp_path / "RAG"
        root.mkdir(exist_ok=True)
        return RagWatchService(
            root=root,
            state_path=tmp_path / "s.json",
            service_context=_FakeCtx(store),
            delete_policy="unindex",
            **kw,
        )

    @pytest.mark.asyncio
    async def test_d2_unindex_removes(self, tmp_path: Path) -> None:
        store = _FakeStore()
        svc = self._unindex_svc(tmp_path, store)
        svc.state.record("h1", rel_path="f/gone.md", doc_id="d1", folder_name="f", size=1)

        await svc.run_once()
        assert store.deleted == ["d1"]
        assert svc.state.get("h1") is None

    @pytest.mark.asyncio
    async def test_d3_guard_blocks_mass_delete(self, tmp_path: Path) -> None:
        """마운트 실패로 폴더가 비어 보이면 색인 전체가 날아간다 — 가드가 막아야 한다."""
        store = _FakeStore()
        svc = self._unindex_svc(tmp_path, store, unindex_guard_ratio=0.25, unindex_guard_min=5)
        for i in range(100):
            svc.state.record(f"h{i}", rel_path=f"f/{i}.md", doc_id=f"d{i}", folder_name="f", size=1)

        await svc.run_once()  # 파일이 전부 없다 → 100건 삭제 시도
        assert store.deleted == [], "대량 삭제가 가드에 막혀야 한다"
        assert len(svc.state) == 100, "상태도 보존되어야 한다"

    @pytest.mark.asyncio
    async def test_d4_guard_allows_normal_delete(self, tmp_path: Path) -> None:
        """가드가 정상 삭제까지 막으면 기능이 무의미해진다."""
        store = _FakeStore()
        svc = self._unindex_svc(tmp_path, store, unindex_guard_ratio=0.25, unindex_guard_min=5)
        root = tmp_path / "RAG"
        # 96건은 디스크에 남기고 4건만 사라진 상황 (4% — 가드 미달)
        for i in range(100):
            p = root / "f" / f"{i}.md"
            if i >= 4:
                write(p, f"내용{i}")
            svc.state.record(f"h{i}", rel_path=f"f/{i}.md", doc_id=f"d{i}", folder_name="f", size=1)

        await svc.run_once()
        assert sorted(store.deleted) == ["d0", "d1", "d2", "d3"]

    @pytest.mark.asyncio
    async def test_d5_guard_min_allows_small_absolute_count(self, tmp_path: Path) -> None:
        """문서가 몇 건뿐일 때 비율 가드가 정상 삭제를 막지 않아야 한다."""
        store = _FakeStore()
        svc = self._unindex_svc(tmp_path, store, unindex_guard_ratio=0.25, unindex_guard_min=5)
        for i in range(3):  # 3건 전부 사라짐 = 100%지만 guard_min(5) 이하
            svc.state.record(f"h{i}", rel_path=f"f/{i}.md", doc_id=f"d{i}", folder_name="f", size=1)

        await svc.run_once()
        assert len(store.deleted) == 3

    @pytest.mark.asyncio
    async def test_d6_guard_disabled_by_zero_ratio(self, tmp_path: Path) -> None:
        store = _FakeStore()
        svc = self._unindex_svc(tmp_path, store, unindex_guard_ratio=0.0)
        for i in range(100):
            svc.state.record(f"h{i}", rel_path=f"f/{i}.md", doc_id=f"d{i}", folder_name="f", size=1)

        await svc.run_once()
        assert len(store.deleted) == 100


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


# ────────────────────────────────────────────────────────────
# 자동 시딩 (CR-41 후속) — 사람이 스크립트를 돌리지 않아도 되게
# ────────────────────────────────────────────────────────────


class _Info:
    def __init__(self, doc_id: str, filename: str, folder_id: str | None) -> None:
        self.doc_id = doc_id
        self.filename = filename
        self.folder_id = folder_id


class TestSeeding:
    @pytest.mark.asyncio
    async def test_g1_seeds_existing_docs(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """시딩이 없으면 감시를 켜는 순간 이미 색인된 파일이 전부 재임베딩된다."""
        root, _store, svc = env
        write(root / "완결보고서" / "a.pdf")

        import app.rag_routes as rr

        monkeypatch.setattr(rr, "_load_folders", lambda: [{"folder_id": "F1", "name": "완결보고서"}])
        monkeypatch.setattr(
            rr, "_list_documents_from_store", lambda _s: [_Info("doc-a", "a.pdf", "F1")]
        )

        assert await svc.seed_from_existing() == 1

        # 시딩됐으므로 스캔에서 인제스트 대상이 아니어야 한다
        async def boom(*_a, **_k):
            raise AssertionError("시딩됐는데 재임베딩이 일어났다")

        monkeypatch.setattr(rr, "ingest_document_bytes", boom)
        await svc.run_once()
        await svc.run_once()

    @pytest.mark.asyncio
    async def test_g2_skips_when_state_not_empty(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """재시작마다 다시 훑을 필요가 없다 — 상태가 있으면 건너뛴다."""
        _root, _store, svc = env
        svc.state.record("h1", rel_path="f/a.md", doc_id="d1", folder_name="f", size=1)

        called = False

        def spy(_s):
            nonlocal called
            called = True
            return []

        import app.rag_routes as rr

        monkeypatch.setattr(rr, "_list_documents_from_store", spy)
        assert await svc.seed_from_existing() == 0
        assert called is False

    @pytest.mark.asyncio
    async def test_g3_unmatched_file_left_for_ingest(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """색인에 없는 파일은 시딩하지 않고 정상 인제스트 대상으로 남겨야 한다."""
        root, _store, svc = env
        write(root / "f" / "new.md")

        import app.rag_routes as rr

        monkeypatch.setattr(rr, "_load_folders", lambda: [{"folder_id": "F1", "name": "f"}])
        monkeypatch.setattr(rr, "_list_documents_from_store", lambda _s: [])

        assert await svc.seed_from_existing() == 0
        assert len(svc.state) == 0

    @pytest.mark.asyncio
    async def test_g4_failure_does_not_block_watching(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """시딩이 실패해도 감시 자체는 살아야 한다 (중복 위험은 로그로 크게 알린다)."""
        root, _store, svc = env
        write(root / "f" / "a.md")

        import app.rag_routes as rr

        def boom(_s):
            raise RuntimeError("스토어 조회 실패")

        monkeypatch.setattr(rr, "_list_documents_from_store", boom)
        assert await svc.seed_from_existing() == 0  # 예외를 삼키고 0을 돌려준다
