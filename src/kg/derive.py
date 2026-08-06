# src/kg/derive.py
"""M_23 8단계 — 관계 유도와 연결성 회복 (스펙 §3 (8), §4.1-B·C).

**LLM을 부르지 않는다.** 스펙 v1은 여기서 청크마다 LLM 관계추출을 돌리려 했으나, 실측 결과
`entity_type` 7종이 §5.2의 Project 관계 7종과 정확히 1:1이었다. 표 하나로 같은 엣지를
얻을 수 있는데 청크 28,976개를 20~30시간 다시 돌릴 이유가 없다. 엔티티↔엔티티 관계
(`ADDRESSES`·`IMPROVES`·`USES_PRIOR_OUTPUT`)만 LLM이 필요하고 그건 2단계로 미룬다.

이 모듈이 진짜로 해결하는 문제는 관계가 아니라 **연결성**이다.

    정확일치 후 엔티티 174,985개 중 160,080개(91.5%)가 단일 문서 전용이다.

그대로 적재하면 CR-34에서 이미 실패한 "별들의 숲" — 문서 하나와 그 문서에만 있는 엔티티들로
이루어진, 서로 이어지지 않은 별 6천 개 — 가 17만 개 규모로 재현된다. 세 장치로 막는다.

1. **target_key 승격** (`APPLIED_TO`). 추출 때 작물·병해충·지역을 정규화해 담아 뒀는데
   지금까지 병합 거부권(R2)에만 쓰이고 그래프에 오르지 않았다. 상위 값이 곧 도메인
   허브다(`벼` 343문서 · `콩` 193 · `토마토` 155). 문서 고유의 긴 엔티티가 이 허브에
   매달리면서 별들의 숲이 실제 네트워크가 된다.
2. **document_frequency**. df=1은 연결력이 없고 df≫1은 변별력이 없다. 한 숫자가 두 문제를
   다 처리한다. **노드를 지우지 않고 가중치로만 쓴다** — 코퍼스가 계속 늘어나므로 오늘의
   df=1이 내일의 허브다.
3. **SHARES_ENTITY**. 중복성 분석이 실제로 묻는 "이 두 과제가 겹치는가"를 엣지로 만든다.
   팬아웃 상한으로 상용구 허브(`산업재산권 출원` 206문서)가 만드는 가짜 유사도를 잘라낸다.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from .candidates import CandidateStore, CanonicalEntity, RelationCandidate
from .config import (
    DOC_TYPE_TO_STATUS,
    ENTITY_TYPE_TO_RELATION,
    NON_ATTRIBUTABLE_STATUSES,
    KnowledgeGraphConfig,
)
from .merge import normalize_name
from .projects import resolve_projects

logger = logging.getLogger(__name__)

StopFn = Callable[[], bool]

STATUS_EXTRACTED = "EXTRACTED"
STATUS_DERIVED = "DERIVED_FROM_DOC_TYPE"
STATUS_UNKNOWN = "UNKNOWN"

# 문서-문서 공유 엣지에서 source_kind로 쓰는 표식. relation_candidates 표를 재사용하되
# 이 값이면 source/target이 canonical_id가 아니라 doc_id다.
SOURCE_KIND_PROJECT = "PROJECT"
SOURCE_KIND_CANONICAL = "CANONICAL"
SOURCE_KIND_DOCUMENT = "DOCUMENT"

RELATION_APPLIED_TO = "APPLIED_TO"
RELATION_SHARES_ENTITY = "SHARES_ENTITY"


def _rid(*parts: str) -> str:
    return "rc_" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _cid(entity_type: str, normalized: str, target_key_norm: str) -> str:
    """`normalize.py`와 동일한 canonical_id 규칙 — 두 곳이 어긋나면 엣지가 끊긴다."""
    raw = "|".join((entity_type, normalized, target_key_norm))
    return "ce_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


@dataclass
class DeriveStats:
    projects: int = 0
    project_relations: int = 0
    applied_to: int = 0
    shares_entity: int = 0
    target_key_entities: int = 0
    boilerplate: int = 0
    non_attributable_skipped: int = 0
    status_extracted: int = 0
    status_derived: int = 0
    seconds: float = 0.0
    stopped: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "projects": self.projects,
            "project_relations": self.project_relations,
            "applied_to": self.applied_to,
            "shares_entity": self.shares_entity,
            "target_key_entities": self.target_key_entities,
            "boilerplate": self.boilerplate,
            "non_attributable_skipped": self.non_attributable_skipped,
            "status_extracted": self.status_extracted,
            "status_derived": self.status_derived,
            "seconds": round(self.seconds, 1),
            "stopped": self.stopped,
        }


def resolve_status(statuses: list[str], document_type: str) -> tuple[str, str]:
    """진술 상태를 정한다 (스펙 §5.3).

    모델이 실제로 채운 값이 있으면 그것을 쓰고, 없으면 문서유형의 사전확률로 유도한다.
    **원본을 덮어쓰는 것이 아니라 별도 값을 만드는 것이다** — 유도값임이 그래프에서
    드러나야 하고, 나중에 재추출하면 실제값으로 갈아끼울 수 있어야 한다.
    """
    real = [s for s in statuses if s and s != "UNCERTAIN"]
    if real:
        # 여러 개면 최빈값. 같은 엔티티가 계획으로도 실적으로도 언급될 수 있다.
        return Counter(real).most_common(1)[0][0], STATUS_EXTRACTED
    derived = DOC_TYPE_TO_STATUS.get(document_type, "")
    if derived:
        return derived, STATUS_DERIVED
    return "UNCERTAIN", STATUS_UNKNOWN


def derive_all(
    store: CandidateStore,
    config: KnowledgeGraphConfig,
    should_stop: StopFn | None = None,
) -> DeriveStats:
    """8단계 전체 — 과제 해석 → df → target_key 승격 → 관계 유도 → 문서 공유 엣지."""
    stats = DeriveStats()
    t0 = time.perf_counter()
    rel_cfg = config.relations
    graph_cfg = config.graph

    # ── 과제 해석 ────────────────────────────────────────────────────────────
    projects = resolve_projects(store, persist=True)
    stats.projects = len(projects)
    doc_to_project: dict[str, str] = {}
    for p in projects:
        for did in p.doc_ids:
            doc_to_project[did] = p.project_id

    docs = {d.doc_id: d for d in store.all_documents()}
    entries = list(store.iter_doc_entities())
    if not entries:
        logger.warning("KG 8단계: doc_entities가 비어 있다 — 6·7단계를 먼저 돌려야 한다")
        return stats
    logger.info("KG 8단계 시작: doc_entities %d · 과제 %d", len(entries), len(projects))

    # ── document_frequency ───────────────────────────────────────────────────
    df: Counter[str] = Counter()
    docs_by_canonical: dict[str, set[str]] = defaultdict(set)
    for de in entries:
        if de.canonical_id:
            docs_by_canonical[de.canonical_id].add(de.doc_id)
    for cid, dset in docs_by_canonical.items():
        df[cid] = len(dset)

    # ── target_key 승격 ──────────────────────────────────────────────────────
    # target_key 값 하나가 RESEARCH_TARGET 정규 엔티티 하나가 된다. 이미 같은 이름의
    # RESEARCH_TARGET이 있으면 그 노드에 그대로 붙는다 — id 규칙이 같아서 자동으로 만난다.
    target_entities: dict[str, CanonicalEntity] = {}
    target_docs: dict[str, set[str]] = defaultdict(set)
    if rel_cfg.link_target_key:
        for de in entries:
            tk = (de.target_key or "").strip()
            if not tk:
                continue
            norm = normalize_name(tk)
            if not norm:
                continue
            tcid = _cid("RESEARCH_TARGET", norm, norm)
            target_docs[tcid].add(de.doc_id)
            if tcid not in target_entities:
                target_entities[tcid] = CanonicalEntity(
                    canonical_id=tcid,
                    entity_type="RESEARCH_TARGET",
                    canonical_name=tk,
                    normalized_name=norm,
                    target_key=tk,
                    review_status="AUTO_APPROVED",
                    from_target_key=True,
                )
        # 기존 엔티티와 겹치면 df를 합산해야 허브 판정이 맞는다.
        for tcid, dset in target_docs.items():
            docs_by_canonical[tcid] |= dset
            df[tcid] = len(docs_by_canonical[tcid])
        store.upsert_canonicals_bulk(list(target_entities.values()))
        stats.target_key_entities = len(target_entities)
        logger.info("KG 8단계 target_key 승격: RESEARCH_TARGET %d개", len(target_entities))

    # ── df·상용구 표시 기록 ──────────────────────────────────────────────────
    # **df만으로는 상용구를 못 가린다.** 첫 실행에서 `벼`(343문서)·`토마토`(201)·`콩`(193)이
    # `산업재산권 출원`(204)과 함께 상용구로 찍혔다. 둘 다 df는 높지만 성격이 정반대다 —
    # 앞의 것들은 우리가 연결성을 위해 **일부러 만든 대상 허브**이고, 검색에서 감점하면
    # target_key 승격의 취지가 무너진다.
    #
    # 구분 기준은 빈도가 아니라 **내용을 담고 있는가**다. target_key에서 올라온 엔티티는
    # 추출기가 "이 연구의 핵심 대상"으로 지목한 것이므로 정의상 연구 주제이지 행정 항목이
    # 아니다. 그래서 df가 아무리 높아도 상용구로 찍지 않는다.
    #
    # (작물 목록을 박지 않는다는 merge.py의 원칙은 여기서도 유효하다 — `벼`가 작물이라서
    #  봐주는 것이 아니라, target_key로 추출됐다는 **구조적 사실** 때문에 봐주는 것이다.)
    bp_threshold = graph_cfg.boilerplate_document_frequency
    metrics: list[tuple[str, int, int, int]] = []
    for cid, freq in df.items():
        from_tk = cid in target_entities
        is_bp = 1 if (freq >= bp_threshold and not from_tk) else 0
        stats.boilerplate += is_bp
        metrics.append((cid, freq, is_bp, 1 if from_tk else 0))
    for i in range(0, len(metrics), graph_cfg.batch_size):
        store.update_canonical_metrics(metrics[i : i + graph_cfg.batch_size])
    logger.info(
        "KG 8단계 df 계산: 엔티티 %d · 상용구(df>=%d) %d",
        len(metrics),
        bp_threshold,
        stats.boilerplate,
    )

    # ── 관계 유도 ────────────────────────────────────────────────────────────
    by_doc: dict[str, list[RelationCandidate]] = defaultdict(list)
    for de in entries:
        if should_stop is not None and should_stop():
            stats.stopped = True
            break
        if not de.canonical_id:
            continue
        meta = docs.get(de.doc_id)
        doc_type = meta.document_type if meta else "UNKNOWN"
        project_id = doc_to_project.get(de.doc_id, f"doc:{de.doc_id}")
        status, source = (
            resolve_status(de.statuses, doc_type)
            if rel_cfg.derive_statement_status
            else (de.statuses[0] if de.statuses else "UNCERTAIN", STATUS_EXTRACTED)
        )
        if source == STATUS_EXTRACTED:
            stats.status_extracted += 1
        elif source == STATUS_DERIVED:
            stats.status_derived += 1

        # 선행연구·인용문을 현재 과제 성과로 귀속시키지 않는다 (스펙 §5.3).
        # 현재 데이터에는 해당 건이 0이지만, 재추출하면 살아나는 방어선이다.
        if status in NON_ATTRIBUTABLE_STATUSES:
            stats.non_attributable_skipped += 1
            continue

        if rel_cfg.derive_from_entity_type:
            rel_type = ENTITY_TYPE_TO_RELATION.get(de.entity_type)
            if rel_type and rel_type in rel_cfg.enabled_relation_types:
                by_doc[de.doc_id].append(
                    RelationCandidate(
                        relation_candidate_id=_rid(project_id, rel_type, de.canonical_id),
                        doc_id=de.doc_id,
                        chunk_id="",
                        project_no=(meta.project_no if meta else ""),
                        source_kind=SOURCE_KIND_PROJECT,
                        source_canonical_id=project_id,
                        relation_type=rel_type,
                        target_canonical_id=de.canonical_id,
                        statement_status=status,
                        status_source=source,
                        mention_count=de.mention_count,
                        confidence=de.max_confidence,
                        state="PENDING",
                    )
                )
                stats.project_relations += 1

        # 엔티티 → 대상(작물·병해충) 연결. 자기 자신에게는 걸지 않는다.
        if rel_cfg.link_target_key and de.target_key:
            norm = normalize_name(de.target_key)
            tcid = _cid("RESEARCH_TARGET", norm, norm) if norm else ""
            if tcid and tcid != de.canonical_id:
                by_doc[de.doc_id].append(
                    RelationCandidate(
                        relation_candidate_id=_rid(de.canonical_id, RELATION_APPLIED_TO, tcid),
                        doc_id=de.doc_id,
                        chunk_id="",
                        project_no=(meta.project_no if meta else ""),
                        source_kind=SOURCE_KIND_CANONICAL,
                        source_canonical_id=de.canonical_id,
                        relation_type=RELATION_APPLIED_TO,
                        target_canonical_id=tcid,
                        statement_status=status,
                        status_source=source,
                        mention_count=de.mention_count,
                        confidence=de.max_confidence,
                        state="PENDING",
                    )
                )
                stats.applied_to += 1

    # ── 문서↔문서 공유 엣지 ──────────────────────────────────────────────────
    shares: list[RelationCandidate] = []
    if graph_cfg.shares_entity_enabled and not stats.stopped:
        shares = _derive_shares_entity(docs_by_canonical, df, graph_cfg)
        stats.shares_entity = len(shares)

    # ── 기록 ────────────────────────────────────────────────────────────────
    # 문서 단위로 지우고 다시 넣는다 (재실행 안전).
    for doc_id, rels in by_doc.items():
        store.replace_relations_for_document(doc_id, rels)
    for i in range(0, len(shares), graph_cfg.batch_size):
        store.insert_relations_bulk(shares[i : i + graph_cfg.batch_size])

    stats.seconds = time.perf_counter() - t0
    logger.info("KG 8단계 완료: %s", stats.as_dict())
    return stats


def _derive_shares_entity(
    docs_by_canonical: dict[str, set[str]],
    df: Counter[str],
    graph_cfg: object,
) -> list[RelationCandidate]:
    """공유 엔티티로 문서쌍을 잇는다 — 중복성 분석의 본체.

    팬아웃 상한이 핵심이다. `산업재산권 출원`은 206문서에 걸쳐 있어서 그대로 두면
    문서쌍 21,115개를 만들어 내는데, 그 쌍들은 "둘 다 특허를 냈다"는 것 말고 아무 의미가
    없다. M_19가 `_RELATED_MAX_FANOUT=15`로 쓰던 IDF 발상을 그대로 가져온다.
    """
    max_fanout = getattr(graph_cfg, "shares_entity_max_fanout", 15)
    min_weight = getattr(graph_cfg, "shares_entity_min_weight", 2.0)
    max_edges = getattr(graph_cfg, "shares_entity_max_edges", 200000)

    pair_weight: dict[tuple[str, str], float] = defaultdict(float)
    pair_count: Counter[tuple[str, str]] = Counter()

    for cid, dset in docs_by_canonical.items():
        n = len(dset)
        if n < 2 or n > max_fanout:
            continue
        # 희소할수록 무겁게 — 두 문서만 공유하는 엔티티가 가장 강한 증거다.
        weight = 1.0 + (max_fanout - n) / max_fanout
        ordered = sorted(dset)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                pair_weight[(a, b)] += weight
                pair_count[(a, b)] += 1

    ranked = sorted(pair_weight.items(), key=lambda kv: -kv[1])
    out: list[RelationCandidate] = []
    for (a, b), w in ranked:
        if w < min_weight:
            break
        if len(out) >= max_edges:
            break
        out.append(
            RelationCandidate(
                relation_candidate_id=_rid(a, RELATION_SHARES_ENTITY, b),
                doc_id=a,
                chunk_id="",
                source_kind=SOURCE_KIND_DOCUMENT,
                source_canonical_id=a,
                relation_type=RELATION_SHARES_ENTITY,
                target_canonical_id=b,
                statement_status="UNCERTAIN",
                status_source=STATUS_UNKNOWN,
                mention_count=pair_count[(a, b)],
                confidence=round(w, 3),
                state="PENDING",
            )
        )
    logger.info(
        "KG 8단계 문서 공유 엣지: 후보쌍 %d → 채택 %d (팬아웃<=%d, 최소가중 %.1f)",
        len(pair_weight),
        len(out),
        max_fanout,
        min_weight,
    )
    return out


__all__ = [
    "RELATION_APPLIED_TO",
    "RELATION_SHARES_ENTITY",
    "SOURCE_KIND_CANONICAL",
    "SOURCE_KIND_DOCUMENT",
    "SOURCE_KIND_PROJECT",
    "STATUS_DERIVED",
    "STATUS_EXTRACTED",
    "DeriveStats",
    "derive_all",
    "resolve_status",
]
