#!/usr/bin/env python
"""M_23 지식그래프 파이프라인 실행 CLI (지침서 23·25장).

    python scripts/kg_run.py smoke --docs 10      # 소규모 검증 (완결보고서·RFP 혼합)
    python scripts/kg_run.py extract --docs 100   # 파일럿
    python scripts/kg_run.py stats                # 후보 저장소 현황
    python scripts/kg_run.py reset                # 후보 저장소 초기화

**전체 실행은 게이트를 통과해야 한다** (스펙 §9). 소규모 평가 결과 없이 --all을 주면
거부한다 — 지침서 0장 "테스트 없이 대규모 전체 문서 처리를 시작하지 않는다".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# 백엔드와 같은 경로 구성. src/agent/builder.py가 `from src.app.config import ...`로
# 절대 임포트를 하므로 프로젝트 루트도 함께 있어야 한다.
for _p in (str(ROOT), str(ROOT / "src"), str(ROOT / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("SAESSAGI_ROOT", str(ROOT))

from kg.candidates import CandidateStore  # noqa: E402
from kg.config import KnowledgeGraphConfig  # noqa: E402
from kg.documents import build_folder_index  # noqa: E402
from kg.extract import DocumentOutcome, ExtractionRunner  # noqa: E402


def load_config() -> tuple[KnowledgeGraphConfig, dict[str, Any]]:
    """conf.yaml의 app.knowledge_graph를 읽는다. 없으면 기본값."""
    import yaml

    raw = yaml.safe_load((ROOT / "conf.yaml").read_text(encoding="utf-8")) or {}
    app = raw.get("app", {}) or {}
    kg_raw = app.get("knowledge_graph") or {}
    return KnowledgeGraphConfig(**kg_raw), app


def build_vector_store(app_cfg: dict[str, Any]) -> Any:
    from vector_search.store import VectorStore

    paths = app_cfg.get("paths", {}) or {}
    db_path = paths.get("vector_store") or "data/vector_store"
    return VectorStore(db_path=str(ROOT / db_path))


def load_folder_index() -> dict[str, Any]:
    path = ROOT / "data" / "rag_folders.json"
    if not path.exists():
        return {}
    return build_folder_index(json.loads(path.read_text(encoding="utf-8")))


async def build_agent(app_cfg: dict[str, Any], cfg: KnowledgeGraphConfig) -> tuple[Any, str]:
    """추출 전용 LLM 클라이언트.

    `GemmaChatAgent.complete_json`을 쓰지 않는다 — 그 구현은 `json_schema` 인자를 받고도
    **쓰지 않아서** 출력이 자유 형식이 되고, 길어져 토큰 한도에서 잘린다(실측: 561문서
    작업에서 JSON 파싱 실패·타임아웃이 반복됐다). 스키마를 실제로 강제하는
    `JsonCompletionClient`를 쓰고, 서버가 지원하는 방식은 한 번 찔러 확인한다.
    """
    from kg.llm import JsonCompletionClient, probe_schema_mode

    from app.config import AppConfig

    app_config = AppConfig(**app_cfg)
    model = cfg.extraction.ollama_model or app_config.ollama.model
    base_url = str(app_config.ollama.base_url).rstrip("/")
    mode = await probe_schema_mode(base_url, model)
    print(f"구조화 출력 방식: {mode}")
    client = JsonCompletionClient(
        base_url,
        model,
        schema_mode=mode,
        max_connections=max(16, cfg.jobs.extraction_concurrency * 4),
    )
    return client, model


STOP_FILE = ROOT / "data" / "run" / "kg_stop"


def documents_in_folder(vstore: Any, folder_name: str) -> list[str]:
    """폴더 이름으로 문서를 고른다 (예: "2025완결보고서")."""
    import json as _j

    folders = _j.loads((ROOT / "data" / "rag_folders.json").read_text(encoding="utf-8"))
    match = [f for f in folders if f.get("name") == folder_name]
    if not match:
        names = ", ".join(f.get("name", "") for f in folders)
        raise SystemExit(f"폴더를 찾을 수 없습니다: {folder_name!r}\n사용 가능: {names}")
    fid = match[0]["folder_id"]
    tbl = getattr(vstore, "_tbl", None)
    if tbl is None:
        return []
    rows = tbl.search().select(["doc_id", "category"]).limit(400_000).to_list()
    return sorted({str(r["doc_id"]) for r in rows if str(r.get("category") or "") == fid})


def pick_documents(vstore: Any, folders: dict[str, Any], count: int, seed: int = 7) -> list[str]:
    """완결보고서와 RFP를 섞어 고른다.

    한쪽만 보면 문서 유형별 특성(계획 vs 실적)을 못 본다. 지침서가 "핵심 분석 대상은
    연구개발계획서와 완결보고서"라고 했으므로 둘 다 들어가야 검증이 성립한다.
    """
    tbl = getattr(vstore, "_tbl", None)
    if tbl is None:
        return []
    rows = tbl.search().select(["doc_id", "category"]).limit(400_000).to_list()
    by_type: dict[str, set[str]] = {}
    for r in rows:
        doc_id = str(r.get("doc_id") or "")
        if not doc_id:
            continue
        info = folders.get(str(r.get("category") or ""))
        dtype = info.document_type if info else "UNKNOWN"
        by_type.setdefault(dtype, set()).add(doc_id)

    rng = random.Random(seed)
    final = sorted(by_type.get("FINAL_REPORT", set()))
    rfp = sorted(by_type.get("RFP", set()))
    n_final = max(1, round(count * 0.6))
    n_rfp = max(1, count - n_final)
    picked = rng.sample(final, min(n_final, len(final))) + rng.sample(rfp, min(n_rfp, len(rfp)))
    return picked[:count]


def report(
    outcomes: list[DocumentOutcome], elapsed: float, store: CandidateStore
) -> dict[str, Any]:
    docs = len(outcomes)
    chunks = sum(len(o.chunks) for o in outcomes)
    calls = sum(c.attempts for o in outcomes for c in o.chunks)
    accepted = sum(o.accepted for o in outcomes)
    rejected = sum(o.rejected for o in outcomes)
    failed_chunks = sum(o.failed_chunks for o in outcomes)
    reasons: dict[str, int] = {}
    for o in outcomes:
        for c in o.chunks:
            for k, v in c.reject_reasons.items():
                reasons[k] = reasons.get(k, 0) + v
    with_project_no = sum(1 for o in outcomes if o.identity.project_no)

    per_chunk = elapsed / chunks if chunks else 0.0
    summary = {
        "documents": docs,
        "states": {
            s: sum(1 for o in outcomes if o.state == s) for s in {o.state for o in outcomes}
        },
        "chunks_processed": chunks,
        "llm_calls": calls,
        "failed_chunks": failed_chunks,
        "accepted_candidates": accepted,
        "rejected_candidates": rejected,
        "reject_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "documents_with_project_no": with_project_no,
        "elapsed_sec": round(elapsed, 1),
        "sec_per_chunk": round(per_chunk, 2),
        "sec_per_document": round(elapsed / docs, 1) if docs else 0.0,
        "store": store.stats(),
    }
    return summary


async def cmd_extract(args: argparse.Namespace) -> int:
    cfg, app_cfg = load_config()
    if args.chunks:
        cfg.extraction.chunks_per_document = args.chunks
    store = CandidateStore(ROOT / cfg.candidate_db_path)
    vstore = build_vector_store(app_cfg)
    folders = load_folder_index()
    agent, model = await build_agent(app_cfg, cfg)

    if getattr(args, "folder", None):
        doc_ids = documents_in_folder(vstore, args.folder)
        print(f"폴더 {args.folder!r}: 문서 {len(doc_ids)}건")
    else:
        doc_ids = args.doc_id or pick_documents(vstore, folders, args.docs, seed=args.seed)
    if not doc_ids:
        print("대상 문서를 찾지 못했습니다.", file=sys.stderr)
        return 1

    # 재개: 이미 끝난 문서는 건너뛴다. 중단 후 다시 돌릴 때 처음부터 하지 않기 위해서다.
    if getattr(args, "resume", False):
        done = {d.doc_id for d in store.documents_by_state("COMPLETED", limit=100_000)}
        before = len(doc_ids)
        doc_ids = [d for d in doc_ids if d not in done]
        print(f"재개: 완료 {before - len(doc_ids)}건 건너뜀 → 남은 {len(doc_ids)}건")
        if not doc_ids:
            print("모두 완료되었습니다.")
            return 0

    # 이전 중단 신호가 남아 있으면 지운다 (안 지우면 시작하자마자 멈춘다)
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.unlink(missing_ok=True)

    print(
        f"대상 {len(doc_ids)}문서 · 모델 {model} · 문서당 청크 {cfg.extraction.chunks_per_document}"
    )
    runner = ExtractionRunner(
        complete_json=agent,
        store=store,
        vector_store=vstore,
        config=cfg,
        folder_index=folders,
        model_name=model,
    )
    runner.set_stop_file(STOP_FILE)
    job_id = f"extract-{int(time.time())}"
    store.start_job(job_id, "extract", scope=f"{len(doc_ids)}docs")
    started = time.monotonic()
    outcomes: list[DocumentOutcome] = []
    try:
        for i, doc_id in enumerate(doc_ids, 1):
            out = await runner.extract_document(doc_id)
            outcomes.append(out)
            done_n = len(outcomes)
            avg = (time.monotonic() - started) / done_n
            left = avg * (len(doc_ids) - done_n) / 60
            print(
                f"  [{done_n}/{len(doc_ids)}] {out.state:13} 청크 {len(out.chunks):2}/{out.total_chunks:4}"
                f" 후보 {out.accepted:3} 거절 {out.rejected:3} {out.elapsed:5.1f}초"
                f" | 잔여 {left:5.0f}분  {doc_id[:40]}",
                flush=True,
            )
            if out.state == "CANCELLED" or STOP_FILE.exists():
                print("\n■ 중단 요청 — 여기까지 저장하고 멈춥니다. 이어서 하려면 --resume")
                break
    except KeyboardInterrupt:
        runner.cancel()
        print("\n중단 요청 — 안전하게 종료합니다.")
    elapsed = time.monotonic() - started

    summary = report(outcomes, elapsed, store)
    store.finish_job(job_id, "COMPLETED", summary)
    out_path = ROOT / "data" / f"kg_smoke_{len(doc_ids)}docs.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {out_path}")
    return 0


def cmd_stop(_: argparse.Namespace) -> int:
    """중단 신호를 남긴다. 실행 중인 배치가 청크 경계에서 스스로 멈춘다.

    프로세스를 죽이지 않는 이유는, 처리 중이던 청크 결과가 날아가고 문서 상태가
    RUNNING에 걸린 채 남기 때문이다.
    """
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("stop", encoding="utf-8")
    print(f"중단 신호를 보냈습니다: {STOP_FILE}")
    print("진행 중인 청크가 끝나면 멈춥니다. 이어서 하려면 --resume 을 붙여 다시 실행하세요.")
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    cfg, _app = load_config()
    store = CandidateStore(ROOT / cfg.candidate_db_path)
    print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    cfg, _app = load_config()
    store = CandidateStore(ROOT / cfg.candidate_db_path)
    counts = store.reset(keep_documents=args.keep_documents)
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="M_23 지식그래프 파이프라인")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("smoke", "extract"):
        p = sub.add_parser(name, help="후보 추출 실행")
        p.add_argument("--docs", type=int, default=10, help="처리할 문서 수")
        p.add_argument("--doc-id", action="append", help="특정 문서만 (여러 번 지정 가능)")
        p.add_argument("--chunks", type=int, help="문서당 청크 예산 (설정 무시)")
        p.add_argument("--seed", type=int, default=7, help="표본 추출 시드")
        p.add_argument("--folder", help='폴더 이름으로 전체 선택 (예: "2025완결보고서")')
        p.add_argument("--resume", action="store_true", help="이미 완료된 문서는 건너뛴다")
        p.set_defaults(func=lambda a: asyncio.run(cmd_extract(a)))

    p = sub.add_parser("stop", help="진행 중인 추출을 안전하게 중단")
    p.set_defaults(func=lambda a: cmd_stop(a))

    p = sub.add_parser("stats", help="후보 저장소 현황")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("reset", help="후보 저장소 초기화")
    p.add_argument("--keep-documents", action="store_true", help="문서 메타는 유지")
    p.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
