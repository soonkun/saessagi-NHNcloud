#!/usr/bin/env python3
"""scripts/bulk_ingest.py — 서버에서 문서를 대량으로 직접 넣는다 (CR-38 후속).

브라우저 업로드는 파일이 인터넷 터널을 왕복해야 해서, 수백 개를 한꺼번에 올리면
터널이 먼저 끊어진다(502/530). 파일이 이미 서버에 있다면 터널을 거칠 이유가 없다.
이 스크립트는 127.0.0.1로 직접 넣으므로 네트워크 영향을 받지 않는다.

사용법:
    # 폴더 통째로
    .venv/bin/python scripts/bulk_ingest.py /경로/문서폴더 --folder 완결보고서

    # 실패한 것만 다시 (이미 들어간 문서는 건너뜀)
    .venv/bin/python scripts/bulk_ingest.py /경로/문서폴더 --folder 완결보고서 --skip-existing

    # 먼저 뭘 넣을지만 확인
    .venv/bin/python scripts/bulk_ingest.py /경로/문서폴더 --dry-run

비밀번호는 conf.yaml의 app.web.auth_password에서 자동으로 읽는다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
import yaml

ALLOWED = {".txt", ".md", ".pdf", ".docx", ".pptx", ".hwpx"}
ROOT = Path(__file__).resolve().parent.parent


def load_web_config() -> tuple[str, str]:
    """conf.yaml에서 (base_url, password)를 읽는다."""
    conf_path = ROOT / "conf.yaml"
    if not conf_path.exists():
        sys.exit(f"conf.yaml이 없습니다: {conf_path}")
    cfg = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
    web = (cfg.get("app") or {}).get("web") or {}
    port = web.get("port", 12393)
    # host가 0.0.0.0이어도 서버 자신에게는 127.0.0.1로 붙는다.
    return f"http://127.0.0.1:{port}", str(web.get("auth_password") or "")


def login(client: httpx.Client, base: str, password: str) -> None:
    """인증이 켜져 있으면 세션 쿠키를 받아둔다."""
    if not password:
        return
    res = client.post(f"{base}/api/auth/login", json={"password": password}, timeout=30)
    if res.status_code == 400:
        return  # 인증 비활성 상태
    if res.status_code != 200:
        sys.exit(f"로그인 실패({res.status_code}). conf.yaml의 auth_password를 확인하세요.")


def existing_doc_ids(client: httpx.Client, base: str) -> set[str]:
    res = client.get(f"{base}/api/rag/documents", timeout=60)
    res.raise_for_status()
    out: set[str] = set()
    for d in res.json():
        for key in ("doc_id", "id", "filename", "name"):
            if isinstance(d, dict) and d.get(key):
                out.add(str(d[key]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="서버에서 문서를 대량으로 직접 인제스트")
    ap.add_argument("directory", help="문서가 들어있는 디렉토리")
    ap.add_argument("--folder", default=None, help="새싹이 문서 폴더 이름 (없으면 자동 생성)")
    ap.add_argument("--skip-existing", action="store_true", help="이미 등록된 문서는 건너뜀")
    ap.add_argument("--dry-run", action="store_true", help="넣지 않고 목록만 출력")
    ap.add_argument("--timeout", type=float, default=600.0, help="파일당 타임아웃(초)")
    args = ap.parse_args()

    src = Path(args.directory).expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"디렉토리가 아닙니다: {src}")

    files = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in ALLOWED)
    if not files:
        sys.exit(f"인제스트할 파일이 없습니다 (허용 확장자: {', '.join(sorted(ALLOWED))})")

    base, password = load_web_config()
    print(f"대상 : {src}")
    print(f"파일 : {len(files)}개")
    print(f"서버 : {base}")
    print(f"폴더 : {args.folder or '(지정 없음)'}")

    if args.dry_run:
        for p in files[:20]:
            print("  -", p.relative_to(src))
        if len(files) > 20:
            print(f"  ... 외 {len(files) - 20}개")
        return

    with httpx.Client(follow_redirects=False) as client:
        login(client, base, password)

        skip: set[str] = set()
        if args.skip_existing:
            skip = existing_doc_ids(client, base)
            print(f"이미 등록됨 : {len(skip)}개 (건너뜀)")

        ok = failed = skipped = 0
        failures: list[tuple[str, str]] = []
        started = time.time()

        for i, path in enumerate(files, 1):
            name = path.name
            if args.skip_existing and (name in skip or path.stem in skip):
                skipped += 1
                continue

            data = {}
            if args.folder:
                data["folder_name"] = args.folder

            try:
                with path.open("rb") as fh:
                    res = client.post(
                        f"{base}/api/rag/documents",
                        files={"file": (name, fh, "application/octet-stream")},
                        data=data,
                        timeout=args.timeout,
                    )
                if res.status_code == 201:
                    ok += 1
                    status = "OK "
                else:
                    failed += 1
                    detail = res.text[:120].replace("\n", " ")
                    failures.append((name, f"{res.status_code} {detail}"))
                    status = f"FAIL({res.status_code})"
            except Exception as exc:  # 파일 하나가 죽어도 전체를 멈추지 않는다
                failed += 1
                failures.append((name, repr(exc)[:120]))
                status = "ERROR"

            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(files) - i) / rate if rate > 0 else 0
            print(
                f"[{i}/{len(files)}] {status} {name[:60]}  "
                f"(성공 {ok} 실패 {failed} 건너뜀 {skipped}, 남은시간 {eta/60:.1f}분)",
                flush=True,
            )

    print("\n" + "=" * 60)
    print(f"완료 — 성공 {ok} / 실패 {failed} / 건너뜀 {skipped}")
    if failures:
        print("\n실패 목록:")
        for name, reason in failures[:30]:
            print(f"  - {name}: {reason}")
        if len(failures) > 30:
            print(f"  ... 외 {len(failures) - 30}건")
    print("=" * 60)


if __name__ == "__main__":
    main()
