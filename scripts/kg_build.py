#!/usr/bin/env python
"""M_23 6~10단계 실행 CLI — 쌓인 후보로 지식그래프를 세운다 (CR-61).

    python scripts/kg_build.py all --dry-run          # 무엇이 올라갈지만 확인
    python scripts/kg_build.py all --folder 2025완결보고서
    python scripts/kg_build.py all                    # 전체
    python scripts/kg_build.py load --purge-legacy    # M_19 키워드 그래프 폐기 후 적재
    python scripts/kg_build.py report                 # 관찰 리포트
    python scripts/kg_build.py reset-derived          # 파생물만 초기화 (후보는 보존)

`kg_run.py`(1~5단계, LLM 추출)와 짝을 이룬다. 이쪽은 **LLM을 부르지 않는다** — 스펙 §4.1.

**게이트** (스펙 §9.1): 6~9단계는 되돌릴 수 있고 몇 분이면 끝나므로 추출 게이트를 그대로
적용하지 않는다. 되돌릴 수 없는 동작은 `--purge-legacy` 하나뿐이고 명시해야만 실행된다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "src"), str(ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("SAESSAGI_ROOT", str(ROOT))

from kg.candidates import CandidateStore  # noqa: E402
from kg.config import KnowledgeGraphConfig  # noqa: E402
from kg.derive import derive_all  # noqa: E402
from kg.neo4j_load import load_graph, load_summary  # noqa: E402
from kg.normalize import (  # noqa: E402
    consolidate_documents,
    counts_by_type,
    normalize_global,
    review_queue,
)
from kg.report import build_report, connectivity_warnings  # noqa: E402

STOP_FILE = ROOT / "data" / "run" / "kg_build_stop"


def load_config() -> tuple[KnowledgeGraphConfig, dict[str, Any]]:
    import yaml

    raw = yaml.safe_load((ROOT / "conf.yaml").read_text(encoding="utf-8")) or {}
    app = raw.get("app", {}) or {}
    return KnowledgeGraphConfig(**(app.get("knowledge_graph") or {})), app


def open_store(cfg: KnowledgeGraphConfig) -> CandidateStore:
    return CandidateStore(ROOT / cfg.candidate_db_path)


def should_stop() -> bool:
    return STOP_FILE.exists()


def clear_stop() -> None:
    if STOP_FILE.exists():
        STOP_FILE.unlink()


def progress(stage: str, done: int, total: int) -> None:
    pct = (done / total * 100) if total else 0.0
    print(f"  [{stage}] {done:,}/{total:,} ({pct:.0f}%)", flush=True)


def folder_doc_ids(store: CandidateStore, folder: str) -> list[str]:
    rows = store._conn.execute(  # noqa: SLF001
        "SELECT doc_id FROM documents WHERE folder_name = ? ORDER BY doc_id", (folder,)
    ).fetchall()
    return [r["doc_id"] for r in rows]


def build_graph_store(app_cfg: dict[str, Any]) -> Any:
    """graphrag 설정의 Neo4j 접속 정보를 그대로 쓴다 — 같은 인스턴스에 적재한다."""
    from kg.graph_store import KgGraphStore

    g = app_cfg.get("graphrag", {}) or {}
    return KgGraphStore(
        uri=g.get("neo4j_uri", "bolt://127.0.0.1:7687"),
        user=g.get("neo4j_user", "neo4j"),
        password=os.environ.get("SAESSAGI_NEO4J_PASSWORD") or g.get("neo4j_password", ""),
        database=g.get("neo4j_database", "neo4j"),
    )


# ── 명령 ──────────────────────────────────────────────────────────────────────


def cmd_consolidate(args: argparse.Namespace) -> int:
    cfg, _app = load_config()
    store = open_store(cfg)
    doc_ids = folder_doc_ids(store, args.folder) if args.folder else None
    stats = consolidate_documents(store, cfg, doc_ids, progress, should_stop)
    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_normalize(args: argparse.Namespace) -> int:
    cfg, _app = load_config()
    if args.no_fuzzy:
        cfg.normalization.fuzzy_enabled = False
    store = open_store(cfg)
    stats = normalize_global(store, cfg, progress, should_stop)
    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    print("유형별:", json.dumps(counts_by_type(store), ensure_ascii=False))
    return 0


def cmd_derive(_: argparse.Namespace) -> int:
    cfg, _app = load_config()
    store = open_store(cfg)
    stats = derive_all(store, cfg, should_stop)
    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    cfg, app = load_config()
    store = open_store(cfg)
    if args.dry_run:
        print(json.dumps(load_summary(store), ensure_ascii=False, indent=2))
        print("\n(dry-run — Neo4j에 아무것도 쓰지 않았습니다)")
        return 0

    graph = build_graph_store(app)
    if not graph.ping():
        print("Neo4j에 연결할 수 없습니다. ./새싹이.sh 로 기동했는지 확인하세요.", file=sys.stderr)
        return 2
    if args.purge_legacy:
        print("M_19 키워드 그래프를 삭제하고 적재합니다 (되돌릴 수 없음).")
    stats = load_graph(
        store,
        graph,
        cfg,
        purge_legacy=args.purge_legacy,
        progress=progress,
        should_stop=should_stop,
    )
    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    print("Neo4j 현황:", json.dumps(graph.stats(), ensure_ascii=False))
    graph.close()
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    cfg, app = load_config()
    store = open_store(cfg)
    clear_stop()
    doc_ids = folder_doc_ids(store, args.folder) if args.folder else None
    t0 = time.time()

    out: dict[str, Any] = {}
    print("── 6단계 문서 단위 통합 ──")
    out["consolidate"] = consolidate_documents(store, cfg, doc_ids, progress, should_stop).as_dict()
    print("── 7단계 전역 정규화 ──")
    out["normalize"] = normalize_global(store, cfg, progress, should_stop).as_dict()
    print("── 8단계 관계 유도 ──")
    out["derive"] = derive_all(store, cfg, should_stop).as_dict()

    if args.dry_run:
        out["load_preview"] = load_summary(store)
        out["note"] = "dry-run — Neo4j에 쓰지 않음"
    else:
        print("── 9단계 Neo4j 적재 ──")
        graph = build_graph_store(app)
        if not graph.ping():
            print("Neo4j 연결 실패 — 6~8단계 결과는 SQLite에 남아 있습니다.", file=sys.stderr)
            out["load"] = {"error": "neo4j_unavailable"}
        else:
            out["load"] = load_graph(
                store,
                graph,
                cfg,
                purge_legacy=args.purge_legacy,
                progress=progress,
                should_stop=should_stop,
            ).as_dict()
            out["neo4j"] = graph.stats()
            graph.close()

    out["total_seconds"] = round(time.time() - t0, 1)
    print("\n" + json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg, _app = load_config()
    store = open_store(cfg)
    report = build_report(store, cfg)
    report["경고"] = connectivity_warnings(report)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    for w in report["경고"]:
        print(f"\n⚠ {w}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n저장: {args.out}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    cfg, _app = load_config()
    store = open_store(cfg)
    rows = review_queue(store, args.limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_reset_derived(args: argparse.Namespace) -> int:
    cfg, _app = load_config()
    store = open_store(cfg)
    if not args.yes:
        print("파생 산출물(doc_entities·canonical_entities·relation_candidates)을 지웁니다.")
        print("추출 후보 216,509건은 보존됩니다. 실행하려면 --yes 를 붙이세요.")
        return 1
    print(json.dumps(store.clear_derived(), ensure_ascii=False, indent=2))
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("stop", encoding="utf-8")
    print(f"중단 신호: {STOP_FILE} — 현재 배치가 끝나면 멈춥니다.")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="M_23 지식그래프 구축 (6~10단계, LLM 없음)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("consolidate", help="6단계 문서 단위 통합")
    p.add_argument("--folder", help='폴더 이름 (예: "2025완결보고서")')
    p.set_defaults(func=cmd_consolidate)

    p = sub.add_parser("normalize", help="7단계 전역 정규화")
    p.add_argument("--no-fuzzy", action="store_true", help="퍼지 병합 생략 (실측 효과 0.5%%)")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("derive", help="8단계 관계 유도 (LLM 없음)")
    p.set_defaults(func=cmd_derive)

    p = sub.add_parser("load", help="9단계 Neo4j 적재")
    p.add_argument("--dry-run", action="store_true", help="적재하지 않고 규모만 출력")
    p.add_argument(
        "--purge-legacy",
        action="store_true",
        help="M_19 Keyword 그래프를 삭제하고 적재 (되돌릴 수 없음)",
    )
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("all", help="6~9단계 한 번에")
    p.add_argument("--folder", help="폴더 이름으로 범위 제한")
    p.add_argument("--dry-run", action="store_true", help="Neo4j 적재 생략")
    p.add_argument("--purge-legacy", action="store_true", help="M_19 Keyword 그래프 삭제")
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("report", help="10단계 관찰 리포트")
    p.add_argument("--out", help="JSON 저장 경로")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("review", help="검토 큐 (블롭 감시·모호 판정)")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("reset-derived", help="파생물만 초기화 (추출 후보는 보존)")
    p.add_argument("--yes", action="store_true", help="확인")
    p.set_defaults(func=cmd_reset_derived)

    p = sub.add_parser("stop", help="진행 중인 구축을 안전하게 중단")
    p.set_defaults(func=cmd_stop)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
