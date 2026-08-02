"""CR-54: 사용자 응답 중에는 배경 인제스트가 쉬는가.

임베딩과 대화·딥 리서치가 같은 GPU를 쓴다. 겹치면 응답이 느려지고, 실제로 GPU가 고갈돼
백엔드가 통째로 죽었다 (E-87). 배경 작업이 먼저 비켜야 한다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from rag_watch import RagWatchService
from rag_watch.activity import conversation_active, is_conversation_active


class TestActivityFlag:
    def test_inactive_by_default(self) -> None:
        assert is_conversation_active() is False

    def test_marks_active_inside_block(self) -> None:
        with conversation_active():
            assert is_conversation_active() is True
        assert is_conversation_active() is False

    def test_nested_conversations_need_all_to_finish(self) -> None:
        """대화가 겹칠 수 있다 — 하나 끝났다고 재개하면 아직 기다리는 쪽이 손해다."""
        with conversation_active():
            with conversation_active():
                assert is_conversation_active() is True
            assert is_conversation_active() is True
        assert is_conversation_active() is False

    def test_resets_on_exception(self) -> None:
        """예외로 턴이 끝나도 표시가 남으면 인제스트가 영원히 멈춘다."""
        with pytest.raises(RuntimeError):
            with conversation_active():
                raise RuntimeError("boom")
        assert is_conversation_active() is False

    def test_thread_safe_counter(self) -> None:
        def worker() -> None:
            for _ in range(200):
                with conversation_active():
                    pass

        ts = [threading.Thread(target=worker) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert is_conversation_active() is False


class _Store:
    def delete_by_doc_id(self, doc_id: str) -> int:
        return 1

    def update_doc_category(self, doc_id: str, category: str | None) -> int:
        return 1

    def delete_by_category(self, category: str) -> int:
        return 1


class _Ctx:
    def __init__(self) -> None:
        self.rag_service = type("R", (), {"_store": _Store()})()
        self.app_config = None


class TestWatcherYields:
    @pytest.mark.asyncio
    async def test_cycle_skipped_while_conversation_active(self, tmp_path: Path) -> None:
        root = tmp_path / "RAG"
        root.mkdir()
        (root / "f").mkdir()
        (root / "f" / "a.md").write_text("내용", encoding="utf-8")
        svc = RagWatchService(root=root, state_path=tmp_path / "s.json", service_context=_Ctx())

        with conversation_active():
            plan = await svc.run_once()
        assert plan.is_empty(), "대화 중에는 아무 것도 하지 않아야 한다"
        # 폴더 동기화조차 시도하지 않았는지 (GPU와 무관해도 같은 프로세스를 붙잡는다)
        assert plan.folders_to_create_in_app == []

    @pytest.mark.asyncio
    async def test_cycle_runs_when_idle(self, tmp_path: Path, monkeypatch: Any) -> None:
        """과보호 방지 — 대화가 없으면 평소대로 돌아야 한다."""
        root = tmp_path / "RAG"
        root.mkdir()
        (root / "f").mkdir()
        svc = RagWatchService(root=root, state_path=tmp_path / "s.json", service_context=_Ctx())

        import app.rag_routes as rr

        env: list[dict[str, str]] = []
        monkeypatch.setattr(rr, "_load_folders", lambda: list(env))
        monkeypatch.setattr(rr, "_save_folders", lambda fs: env.__setitem__(slice(None), fs))
        monkeypatch.setattr(rr, "_ensure_folder_dir", lambda fid: None)

        plan = await svc.run_once()
        assert plan.folders_to_create_in_app == ["f"], "쉬는 동안엔 평소처럼 동기화한다"
