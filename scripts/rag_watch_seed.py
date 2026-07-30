#!/usr/bin/env python3
"""scripts/rag_watch_seed.py — 이미 인제스트된 문서를 감시 상태에 등록한다 (M_22 / CR-41).

감시를 처음 켤 때 딱 한 번 실행한다.

왜 필요한가: 이미 색인된 파일을 감시 폴더로 옮기면 감시자는 신규로 보고 **전부 다시
임베딩한다.** 270건이면 색인이 두 배로 불고 검색 결과에 중복이 뜬다. 이 스크립트가
"이 파일은 이미 이 doc_id로 들어가 있다"를 상태 파일에 미리 기록해 그것을 막는다.

매칭 기준은 (파일명, 폴더명)이다. 같은 이름이 여러 폴더에 있어도 폴더로 구분된다.

사용법:
    .venv/bin/python scripts/rag_watch_seed.py            # 실제 기록
    .venv/bin/python scripts/rag_watch_seed.py --dry-run  # 무엇이 매칭되는지만 확인
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rag_watch.scanner import collect_candidates  # noqa: E402
from rag_watch.state import WatchState, file_digest  # noqa: E402


def load_conf() -> dict:
    conf = ROOT / "conf.yaml"
    if not conf.exists():
        sys.exit(f"conf.yaml이 없습니다: {conf}")
    return yaml.safe_load(conf.read_text(encoding="utf-8")) or {}


def fetch_documents(base: str, password: str) -> list[dict]:
    """등록된 문서 목록 (doc_id, 파일명, 폴더)."""
    with httpx.Client(follow_redirects=False, timeout=120) as c:
        if password:
            res = c.post(f"{base}/api/auth/login", json={"password": password})
            if res.status_code not in (200, 400):
                sys.exit(f"로그인 실패({res.status_code}) — conf.yaml의 비밀번호 확인")
        res = c.get(f"{base}/api/rag/documents")
        res.raise_for_status()
        return list(res.json())


def main() -> None:
    ap = argparse.ArgumentParser(description="감시 상태 시딩 (중복 인제스트 방지)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_conf()
    app = cfg.get("app") or {}
    web = app.get("web") or {}
    watch = app.get("rag_watch") or {}

    root_str = watch.get("root") or ""
    if not root_str:
        sys.exit("app.rag_watch.root가 설정되지 않았습니다.")
    root = Path(root_str)
    if not root.is_dir():
        sys.exit(f"감시 루트가 없습니다: {root}")

    base = f"http://127.0.0.1:{web.get('port', 12393)}"
    password = str(web.get("auth_password") or "") if web.get("auth_enabled") else ""

    docs = fetch_documents(base, password)
    # (파일명, 폴더명) → doc_id.  폴더 이름은 folder_name 또는 folder 필드에 온다.
    folders = {f["folder_id"]: f["name"] for f in (_load_folder_map(base, password))}
    index: dict[tuple[str, str | None], str] = {}
    for d in docs:
        name = str(d.get("filename") or d.get("doc_name") or "")
        fid = d.get("folder_id") or None
        fname = folders.get(fid) if fid else None
        doc_id = str(d.get("doc_id") or "")
        if name and doc_id:
            index[(name, fname)] = doc_id

    candidates, _ = collect_candidates(root)
    state = WatchState(Path(app.get("paths", {}).get("data_dir", "data")) / "rag_watch_state.json")

    matched = unmatched = already = 0
    for cand in candidates:
        key = (cand.path.name, cand.folder_name)
        doc_id = index.get(key)
        if doc_id is None:
            unmatched += 1
            continue
        digest = file_digest(cand.path)
        if state.get(digest) is not None:
            already += 1
            continue
        matched += 1
        if not args.dry_run:
            state.record(
                digest,
                rel_path=cand.rel_path,
                doc_id=doc_id,
                folder_name=cand.folder_name,
                size=cand.size,
            )

    if not args.dry_run and matched:
        state.save()

    print(f"감시 루트   : {root}")
    print(f"등록된 문서 : {len(docs)}건")
    print(f"디스크 파일 : {len(candidates)}건")
    print(f"매칭(시딩)  : {matched}건" + ("  [dry-run — 기록 안 함]" if args.dry_run else ""))
    print(f"이미 기록됨 : {already}건")
    print(f"미매칭      : {unmatched}건 → 감시가 켜지면 새로 인제스트된다")


def _load_folder_map(base: str, password: str) -> list[dict]:
    with httpx.Client(follow_redirects=False, timeout=60) as c:
        if password:
            c.post(f"{base}/api/auth/login", json={"password": password})
        res = c.get(f"{base}/api/rag/folders")
        res.raise_for_status()
        return list(res.json())


if __name__ == "__main__":
    main()
