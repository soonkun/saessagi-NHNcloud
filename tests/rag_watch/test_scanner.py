"""M_22 스캐너 테스트 (CR-41) — 판단 규칙만 검증한다(임베딩 없음)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from rag_watch.scanner import build_plan, collect_candidates, sanitize_folder_name
from rag_watch.state import WatchState, file_digest


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    d = tmp_path / "RAG"
    d.mkdir()
    return d


@pytest.fixture()
def state(tmp_path: Path) -> WatchState:
    return WatchState(tmp_path / "state.json")


def write(path: Path, text: str = "내용") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def plan_twice(root: Path, state: WatchState, **kw):
    """안정화 확인 때문에 최초 발견은 보류된다 — 두 번 스캔해 실제 계획을 얻는다."""
    build_plan(root, state, app_folder_names=kw.get("app_folder_names", set()), max_per_cycle=kw.get("max_per_cycle", 20))
    return build_plan(
        root,
        state,
        app_folder_names=kw.get("app_folder_names", set()),
        max_per_cycle=kw.get("max_per_cycle", 20),
    )


# ────────────────────────────────────────────────────────────
# 폴더명 새니타이즈
# ────────────────────────────────────────────────────────────


class TestSanitize:
    @pytest.mark.parametrize("bad", ["", "  ", ".", "..", "a/b", "a\\b", "/", "\\"])
    def test_s1_rejects_unsafe(self, bad: str) -> None:
        assert sanitize_folder_name(bad) is None

    def test_s2_trims_whitespace(self) -> None:
        assert sanitize_folder_name("  완결보고서  ") == "완결보고서"

    def test_s3_keeps_korean_and_spaces(self) -> None:
        assert sanitize_folder_name("2026 사업 보고") == "2026 사업 보고"


# ────────────────────────────────────────────────────────────
# 수집
# ────────────────────────────────────────────────────────────


class TestCollect:
    def test_c1_subdir_becomes_folder(self, root: Path) -> None:
        write(root / "완결보고서" / "a.md")
        cands, folders = collect_candidates(root)
        assert folders == {"완결보고서"}
        assert cands[0].folder_name == "완결보고서"

    def test_c2_root_file_has_no_folder(self, root: Path) -> None:
        write(root / "loose.md")
        cands, folders = collect_candidates(root)
        assert folders == set()
        assert cands[0].folder_name is None

    def test_c3_nested_flattens_to_top_subdir(self, root: Path) -> None:
        """앱 폴더 모델이 1단이므로 깊은 경로는 최상위 서브디렉토리로 평탄화한다."""
        write(root / "RFP" / "2026" / "q1" / "deep.md")
        cands, _ = collect_candidates(root)
        assert cands[0].folder_name == "RFP"

    @pytest.mark.parametrize("name", [".hidden.md", "~$temp.docx", "a.md.tmp", "b.pdf.part"])
    def test_c4_ignores_temp_files(self, root: Path, name: str) -> None:
        write(root / "폴더" / name)
        cands, _ = collect_candidates(root)
        assert cands == []


# ────────────────────────────────────────────────────────────
# 계획
# ────────────────────────────────────────────────────────────


class TestPlan:
    def test_p1_new_file_ingested_after_stabilizing(self, root: Path, state: WatchState) -> None:
        write(root / "완결보고서" / "a.md")

        first = build_plan(root, state, app_folder_names=set(), max_per_cycle=20)
        assert first.to_ingest == [] and len(first.unstable) == 1, "전송 중일 수 있어 1회는 보류"

        second = build_plan(root, state, app_folder_names={"완결보고서"}, max_per_cycle=20)
        assert len(second.to_ingest) == 1

    def test_p2_growing_file_stays_unstable(self, root: Path, state: WatchState) -> None:
        """SFTP 전송 중 파일을 인제스트하면 잘린 문서가 색인된다."""
        p = write(root / "f" / "big.md", "a")
        build_plan(root, state, app_folder_names={"f"}, max_per_cycle=20)

        time.sleep(0.01)
        p.write_text("aaaaaaaaaa", encoding="utf-8")
        os.utime(p, (time.time(), time.time()))

        plan = build_plan(root, state, app_folder_names={"f"}, max_per_cycle=20)
        assert plan.to_ingest == [] and len(plan.unstable) == 1

    def test_p3_already_ingested_skipped(self, root: Path, state: WatchState) -> None:
        p = write(root / "f" / "a.md")
        state.record(
            file_digest(p), rel_path="f/a.md", doc_id="d1", folder_name="f", size=p.stat().st_size
        )
        plan = plan_twice(root, state, app_folder_names={"f"})
        assert plan.to_ingest == [] and plan.skipped == 1

    def test_p4_moved_file_is_move_not_reingest(self, root: Path, state: WatchState) -> None:
        """내용이 같은데 폴더만 바뀌면 재임베딩하지 않는다 — 수백 건이면 치명적."""
        p = write(root / "새폴더" / "a.md")
        state.record(
            file_digest(p),
            rel_path="옛폴더/a.md",
            doc_id="d1",
            folder_name="옛폴더",
            size=p.stat().st_size,
        )
        plan = plan_twice(root, state, app_folder_names={"새폴더", "옛폴더"})
        assert plan.to_ingest == []
        assert len(plan.to_move) == 1
        cand, doc_id = plan.to_move[0]
        assert doc_id == "d1" and cand.folder_name == "새폴더"

    def test_p5_creates_app_folder_for_new_dir(self, root: Path, state: WatchState) -> None:
        (root / "신규폴더").mkdir()
        plan = build_plan(root, state, app_folder_names=set(), max_per_cycle=20)
        assert plan.folders_to_create_in_app == ["신규폴더"]

    def test_p6_creates_disk_folder_for_ui_folder(self, root: Path, state: WatchState) -> None:
        """UI에서 만든 폴더가 파일탐색기에도 보여야 파일을 넣을 수 있다."""
        plan = build_plan(root, state, app_folder_names={"UI에서만든것"}, max_per_cycle=20)
        assert plan.folders_to_create_on_disk == ["UI에서만든것"]

    def test_p7_max_per_cycle_defers_rest(self, root: Path, state: WatchState) -> None:
        for i in range(5):
            write(root / "f" / f"{i}.md", f"내용{i}")
        plan = plan_twice(root, state, app_folder_names={"f"}, max_per_cycle=2)
        assert len(plan.to_ingest) == 2
        assert len(plan.deferred) == 3

    def test_p8_unsupported_extension_skipped(self, root: Path, state: WatchState) -> None:
        write(root / "f" / "a.exe")
        write(root / "f" / "b.zip")
        plan = plan_twice(root, state, app_folder_names={"f"})
        assert plan.to_ingest == [] and plan.skipped == 2

    def test_p9_missing_file_reported(self, root: Path, state: WatchState) -> None:
        state.record(
            "deadbeef", rel_path="f/gone.md", doc_id="d9", folder_name="f", size=10
        )
        plan = build_plan(root, state, app_folder_names=set(), max_per_cycle=20)
        assert plan.missing_digests == ["deadbeef"]

    def test_p9b_unstable_file_not_reported_missing(self, root: Path, state: WatchState) -> None:
        """회귀 방지: 안정화 대기 중인 파일을 '사라짐'으로 보면 재시작 때마다
        delete_policy=unindex가 색인 전체를 지운다 (실제로 발생했던 버그)."""
        p = write(root / "완결보고서" / "a.md")
        state.record(
            file_digest(p),
            rel_path="완결보고서/a.md",
            doc_id="d1",
            folder_name="완결보고서",
            size=p.stat().st_size,
        )

        # 첫 스캔 — 모든 파일이 안정화 대기라 해시가 계산되지 않는다
        first = build_plan(root, state, app_folder_names={"완결보고서"}, max_per_cycle=20)
        assert first.unstable, "전제: 첫 주기엔 보류 상태여야 한다"
        assert first.missing_digests == [], "파일이 디스크에 있으므로 사라진 게 아니다"

    def test_p9c_still_detects_real_deletion(self, root: Path, state: WatchState) -> None:
        """경로 확인을 넣었어도 실제 삭제는 여전히 잡아야 한다."""
        p = write(root / "f" / "a.md")
        digest = file_digest(p)
        state.record(digest, rel_path="f/a.md", doc_id="d1", folder_name="f", size=6)
        p.unlink()

        plan = build_plan(root, state, app_folder_names={"f"}, max_per_cycle=20)
        assert plan.missing_digests == [digest]

    def test_p10_unsafe_dirname_skipped(self, root: Path, state: WatchState) -> None:
        d = root / ".."
        # 실제 ".." 디렉토리는 만들 수 없으므로 이름만 검증 (collect가 sanitize를 거친다)
        assert sanitize_folder_name(d.name) is None

    def test_p11_empty_plan_detected(self, root: Path, state: WatchState) -> None:
        assert build_plan(root, state, app_folder_names=set(), max_per_cycle=20).is_empty()
