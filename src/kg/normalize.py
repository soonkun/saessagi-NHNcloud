# src/kg/normalize.py
"""M_23 6·7단계 — 문서 단위 통합과 전역 정규화 (스펙 §3 (6)(7), §6).

`merge.py`는 "두 이름이 같은 것인가"를 판정하는 규칙만 갖고 있고, 그것을 216,509건에
실제로 적용하는 곳이 여기다. `merge.consolidate()`는 CR-60에서 구현·테스트까지 끝났으나
호출자가 없었다 — 이 모듈이 그 호출자다.

**연쇄 병합을 하지 않는다.**

    A~B 이고 B~C 여도 A~C 가 아니면 셋을 한 덩어리로 만들지 않는다.

CR-36에서 union-find 연쇄가 3만 용어를 208개 blob으로 붕괴시켰다("AI"·"3D프린팅"·
"CRISPR"가 한 노드가 됐다). 그래서 여기서는 신규 항목을 그룹의 **대표 이름과만** 비교한다.
대표와 SAME이 아니면 붙지 않는다. 이 방식은 `consolidate()`가 문서 단위에서 쓰는 것과
같고, 비교 횟수도 O(n × 대표수)로 떨어진다.

그 위에 **블롭 감시**를 더 얹는다 — 한 정규 엔티티가 표기를 상한(기본 50) 넘게 흡수하면
흡수를 멈추고 검토 큐로 보낸다. 연쇄를 막았는데도 blob이 자란다면 규칙 자체가 틀린 것이고,
그때는 조용히 번지는 대신 눈에 띄어야 한다.

**퍼지 병합의 실측 효과는 0.5%다** (스펙 §4.1-A: RESEARCH_TARGET 39,795 → 39,601).
정확일치가 일을 다 한다. 그래서 퍼지는 켜 두되 껐을 때와 결과가 거의 같다는 것을 알고 쓴다.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .candidates import CandidateStore, CanonicalEntity, DocEntity
from .config import KgNormalizationConfig, KnowledgeGraphConfig
from .merge import MergeInput, MergeRules, analyze_name, consolidate, merge_decision, normalize_name

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]
StopFn = Callable[[], bool]


def _hid(prefix: str, *parts: str) -> str:
    """결정적 id — 같은 입력이면 같은 id (재실행 안전, `extract.py`의 `ec_` 규칙과 통일)."""
    raw = "|".join(parts)
    return prefix + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


@dataclass
class ConsolidateStats:
    """6단계 결과."""

    documents: int = 0
    candidates: int = 0
    doc_entities: int = 0
    review_required: int = 0
    seconds: float = 0.0
    stopped: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "documents": self.documents,
            "candidates": self.candidates,
            "doc_entities": self.doc_entities,
            "review_required": self.review_required,
            "seconds": round(self.seconds, 1),
            "stopped": self.stopped,
        }


@dataclass
class NormalizeStats:
    """7단계 결과."""

    doc_entities: int = 0
    canonical_entities: int = 0
    exact_merged: int = 0
    fuzzy_merged: int = 0
    blob_capped: int = 0
    review_required: int = 0
    comparisons: int = 0
    seconds: float = 0.0
    stopped: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "doc_entities": self.doc_entities,
            "canonical_entities": self.canonical_entities,
            "exact_merged": self.exact_merged,
            "fuzzy_merged": self.fuzzy_merged,
            "blob_capped": self.blob_capped,
            "review_required": self.review_required,
            "comparisons": self.comparisons,
            "seconds": round(self.seconds, 1),
            "stopped": self.stopped,
        }


def _rules_from_config(config: KnowledgeGraphConfig) -> MergeRules:
    n = config.normalization
    return MergeRules(
        string_similarity_threshold=n.string_similarity_threshold,
        prevent_cross_type_merge=n.prevent_cross_type_merge,
        prevent_different_target_merge=n.prevent_different_target_merge,
    )


def _best_name(candidate_name: str, surface: str) -> str:
    """추출기가 제안한 정규명이 있으면 그걸 쓰고, 없으면 원문 표기."""
    return (candidate_name or "").strip() or (surface or "").strip()


# ── 6단계: 문서 단위 통합 ─────────────────────────────────────────────────────


def consolidate_documents(
    store: CandidateStore,
    config: KnowledgeGraphConfig,
    doc_ids: Sequence[str] | None = None,
    progress: ProgressFn | None = None,
    should_stop: StopFn | None = None,
) -> ConsolidateStats:
    """문서마다 표기 변형을 묶어 `doc_entities`를 만든다 (스펙 §3 (6)).

    문서 경계를 넘지 않는다 — 문서를 넘는 병합은 근거가 더 필요하므로 7단계에서 한다.
    문서 단위로 커밋하므로 중간에 멈춰도 끝난 문서는 남는다.
    """
    rules = _rules_from_config(config)
    min_conf = config.extraction.minimum_confidence
    stats = ConsolidateStats()
    t0 = time.perf_counter()

    targets = list(doc_ids) if doc_ids is not None else _all_document_ids(store)
    total = len(targets)
    logger.info("KG 6단계 문서 단위 통합 시작: 문서 %d건", total)

    for i, doc_id in enumerate(targets, 1):
        if should_stop is not None and should_stop():
            stats.stopped = True
            logger.info("KG 6단계 중단 요청 — %d/%d 문서에서 멈춤", i - 1, total)
            break

        candidates = store.candidates_for_document(doc_id)
        # REJECTED는 검증에서 떨어진 것이라 그래프에 올리지 않는다 (스펙 §2 원칙 2).
        usable = [c for c in candidates if c.state != "REJECTED"]
        if not usable:
            continue

        items = [
            (
                c.candidate_id,
                MergeInput(
                    name=_best_name(c.canonical_name_candidate, c.surface_form),
                    entity_type=c.entity_type,
                    target_key=c.target_key,
                    confidence=c.confidence,
                ),
                c.statement_status,
            )
            for c in usable
        ]
        groups = consolidate(items, rules, min_conf)

        entities: list[DocEntity] = []
        seen_ids: set[str] = set()
        for g in groups:
            normalized = normalize_name(g.canonical_name, rules)
            deid = _hid("de_", doc_id, g.entity_type, normalized, g.target_key)
            # 해시 충돌·동일키 그룹이 생겨도 행이 사라지지 않게 한다.
            if deid in seen_ids:
                deid = _hid(
                    "de_", doc_id, g.entity_type, normalized, g.target_key, str(len(seen_ids))
                )
            seen_ids.add(deid)
            if g.review_required:
                stats.review_required += 1
            entities.append(
                DocEntity(
                    doc_entity_id=deid,
                    doc_id=doc_id,
                    entity_type=g.entity_type,
                    canonical_name_candidate=g.canonical_name,
                    normalized_name=normalized,
                    target_key=g.target_key,
                    aliases=g.aliases[: config.normalization.max_aliases_stored],
                    statuses=g.statuses,
                    source_candidate_ids=g.member_ids,
                    mention_count=len(g.member_ids),
                    max_confidence=g.max_confidence,
                    review_required=g.review_required,
                    state="PENDING",
                )
            )

        store.replace_doc_entities(doc_id, entities)
        stats.documents += 1
        stats.candidates += len(usable)
        stats.doc_entities += len(entities)

        if progress is not None and (i % 100 == 0 or i == total):
            progress("consolidate", i, total)

    stats.seconds = time.perf_counter() - t0
    logger.info("KG 6단계 완료: %s", stats.as_dict())
    return stats


def _all_document_ids(store: CandidateStore) -> list[str]:
    """추출이 끝난 문서만 대상으로 한다."""
    rows = store._conn.execute(  # noqa: SLF001 — 같은 패키지 내부 조회
        "SELECT doc_id FROM documents WHERE extract_state IN ('COMPLETED','PARTIAL_FAILED')"
        " ORDER BY doc_id"
    ).fetchall()
    return [r["doc_id"] for r in rows]


# ── 7단계: 전역 정규화 ────────────────────────────────────────────────────────


@dataclass
class _Proto:
    """정규 엔티티가 되기 전 묶음. 대표 이름 하나로 대표된다."""

    entity_type: str
    name: str
    target_key: str
    core: str
    suffix_class: str
    surface_counts: Counter[str] = field(default_factory=Counter)
    member_ids: list[str] = field(default_factory=list)
    doc_ids: set[str] = field(default_factory=set)
    mention_count: int = 0
    max_confidence: float = 0.0
    review_required: bool = False
    blob_capped: bool = False

    def absorb(self, de: DocEntity, limit: int) -> bool:
        """흡수 성공 여부. 상한을 넘으면 받지 않고 blob 플래그를 세운다."""
        if (
            len(self.surface_counts) >= limit
            and de.canonical_name_candidate not in self.surface_counts
        ):
            self.blob_capped = True
            self.review_required = True
            return False
        self.surface_counts[de.canonical_name_candidate] += max(de.mention_count, 1)
        for a in de.aliases:
            if a and a not in self.surface_counts:
                self.surface_counts[a] += 0
        self.member_ids.append(de.doc_entity_id)
        self.doc_ids.add(de.doc_id)
        self.mention_count += de.mention_count
        self.max_confidence = max(self.max_confidence, de.max_confidence)
        if de.review_required:
            self.review_required = True
        if not self.target_key and de.target_key:
            self.target_key = de.target_key
        return True


def normalize_global(
    store: CandidateStore,
    config: KnowledgeGraphConfig,
    progress: ProgressFn | None = None,
    should_stop: StopFn | None = None,
) -> NormalizeStats:
    """문서 단위 엔티티를 코퍼스 전체에서 하나로 모은다 (스펙 §3 (7), §6).

    1) 정확일치 — `(유형, core, 접미어성격)` 버킷. 여기서 대부분이 접힌다.
    2) 버킷 안 대표자 비교 — `merge_decision`이 target_key 거부권(R2)을 집행한다.
       같은 이름이라도 작물이 다르면 갈라진다.
    3) 퍼지 — 토큰 역색인 블로킹 후 **대표와만** 비교. 실측 효과 0.5%.
    """
    n = config.normalization
    rules = _rules_from_config(config)
    stats = NormalizeStats()
    t0 = time.perf_counter()

    logger.info("KG 7단계 전역 정규화 시작")
    entries = list(store.iter_doc_entities())
    stats.doc_entities = len(entries)
    if not entries:
        logger.warning("KG 7단계: doc_entities가 비어 있다 — 6단계를 먼저 돌려야 한다")
        return stats

    # 많이 언급된 것이 대표가 되도록 미리 정렬한다 (결정적 순서).
    entries.sort(key=lambda e: (-e.mention_count, len(e.canonical_name_candidate), e.doc_entity_id))

    # ── (1)(2) 정확일치 버킷 + 대표자 비교 ──────────────────────────────────
    protos: list[_Proto] = []
    buckets: dict[tuple[str, str, str], list[int]] = defaultdict(list)

    for idx, de in enumerate(entries):
        if should_stop is not None and idx % 5000 == 0 and should_stop():
            stats.stopped = True
            logger.info("KG 7단계 중단 요청 — 정확일치 %d/%d", idx, len(entries))
            break

        a = analyze_name(de.canonical_name_candidate, rules)
        key = (de.entity_type, a.core or a.normalized, a.suffix_class)
        placed = False
        for pi in buckets[key]:
            p = protos[pi]
            stats.comparisons += 1
            decision = merge_decision(
                MergeInput(p.name, p.entity_type, p.target_key, 1.0),
                MergeInput(
                    de.canonical_name_candidate, de.entity_type, de.target_key, de.max_confidence
                ),
                rules,
            )
            if decision.auto_merge and p.absorb(de, n.max_members_per_canonical):
                stats.exact_merged += 1
                placed = True
                break
        if not placed:
            p = _Proto(
                entity_type=de.entity_type,
                name=de.canonical_name_candidate,
                target_key=de.target_key,
                core=a.core or a.normalized,
                suffix_class=a.suffix_class,
            )
            p.absorb(de, n.max_members_per_canonical)
            protos.append(p)
            buckets[key].append(len(protos) - 1)

        # 마지막 눈금을 반드시 찍는다 (E-97). `enumerate`가 0부터라 `% 20000`만 보면
        # 총건수가 2만 배수가 아닌 한 꼬리가 남아, 화면이 영원히 `360,000/363,235`에서
        # 멈춘 것처럼 보인다. 6단계(:201)는 `i == total`을 함께 보는데 여기만 빠졌었다.
        done = idx + 1
        if progress is not None and (done % 20000 == 0 or done == len(entries)):
            progress("normalize", done, len(entries))

    logger.info("KG 7단계 정확일치 후: %d개 (doc_entities %d)", len(protos), len(entries))

    # ── (3) 퍼지 — 토큰 블로킹 + 대표자 비교 ────────────────────────────────
    if n.fuzzy_enabled and not stats.stopped:
        protos = _fuzzy_pass(protos, rules, n, stats, should_stop, progress)

    # ── 기록 ────────────────────────────────────────────────────────────────
    canonicals: list[CanonicalEntity] = []
    links: list[tuple[str, str, str]] = []
    for p in protos:
        normalized = normalize_name(p.name, rules)
        cid = _hid("ce_", p.entity_type, normalized, normalize_name(p.target_key, rules))
        # 대표 이름 재선정 — 최빈 표기, 동률이면 짧은 쪽 (consolidate와 같은 규칙)
        if p.surface_counts:
            p.name = min(p.surface_counts.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0]
        review = "REVIEW_REQUIRED" if p.review_required else "AUTO_APPROVED"
        if p.blob_capped:
            stats.blob_capped += 1
        if p.review_required:
            stats.review_required += 1
        aliases = [
            s for s, _ in p.surface_counts.most_common(n.max_aliases_stored) if s and s != p.name
        ]
        canonicals.append(
            CanonicalEntity(
                canonical_id=cid,
                entity_type=p.entity_type,
                canonical_name=p.name,
                normalized_name=normalize_name(p.name, rules),
                target_key=p.target_key,
                aliases=aliases,
                review_status=review,
                mention_count=p.mention_count,
            )
        )
        state = "REVIEW_REQUIRED" if p.review_required else "MATCHED"
        links.extend((deid, cid, state) for deid in p.member_ids)

    # canonical_id가 겹치면(같은 유형·정규명·대상) 하나로 합쳐 UNIQUE 충돌을 피한다.
    canonicals = _dedupe_canonicals(canonicals)

    _write_in_batches(store, canonicals, links, config.graph.batch_size)
    stats.canonical_entities = len(canonicals)
    stats.seconds = time.perf_counter() - t0
    logger.info("KG 7단계 완료: %s", stats.as_dict())
    return stats


def _fuzzy_pass(
    protos: list[_Proto],
    rules: MergeRules,
    n_cfg: KgNormalizationConfig,
    stats: NormalizeStats,
    should_stop: StopFn | None,
    progress: ProgressFn | None = None,
) -> list[_Proto]:
    """토큰 역색인으로 후보군을 좁힌 뒤 **대표와만** 비교해 붙인다.

    흔한 토큰('이용한'·'통한')은 색인에서 빼야 후보군이 폭발하지 않는다. 실측상
    문서빈도 2000 초과 토큰은 단 1개뿐이라 이 필터는 싸고 효과적이다.

    **진행률을 반드시 보고한다 (E-97).** 7단계에서 가장 오래 걸리는 구간인데 여기서
    아무 신호도 안 내보내서, 사용자 화면이 직전 단계 마지막 눈금에 8분간 얼어붙어
    "멈춘 것 아니냐"는 문의가 왔다. 조용히 오래 도는 것은 죽은 것과 구분되지 않는다.
    """
    df_max = n_cfg.blocking_max_document_frequency
    max_cand = n_cfg.max_block_candidates
    member_cap = n_cfg.max_members_per_canonical

    df: Counter[tuple[str, str]] = Counter()
    for p in protos:
        for tok in set(p.core.split()):
            df[(p.entity_type, tok)] += 1

    index: dict[tuple[str, str], list[int]] = defaultdict(list)
    kept: list[_Proto] = []

    for i, p in enumerate(protos):
        if should_stop is not None and i % 5000 == 0 and should_stop():
            stats.stopped = True
            break
        if progress is not None and ((i + 1) % 10000 == 0 or i + 1 == len(protos)):
            progress("normalize:fuzzy", i + 1, len(protos))
        toks = [t for t in set(p.core.split()) if df[(p.entity_type, t)] <= df_max]
        cand: Counter[int] = Counter()
        for t in toks:
            for ki in index[(p.entity_type, t)]:
                cand[ki] += 1

        hit = -1
        for ki, _shared in cand.most_common(max_cand):
            q = kept[ki]
            stats.comparisons += 1
            decision = merge_decision(
                MergeInput(q.name, q.entity_type, q.target_key, 1.0),
                MergeInput(p.name, p.entity_type, p.target_key, 1.0),
                rules,
            )
            if decision.auto_merge:
                hit = ki
                break

        if hit >= 0:
            q = kept[hit]
            if len(q.surface_counts) + len(p.surface_counts) > member_cap:
                q.blob_capped = True
                q.review_required = True
            else:
                q.surface_counts.update(p.surface_counts)
                q.member_ids.extend(p.member_ids)
                q.doc_ids |= p.doc_ids
                q.mention_count += p.mention_count
                q.max_confidence = max(q.max_confidence, p.max_confidence)
                q.review_required = q.review_required or p.review_required
                if not q.target_key and p.target_key:
                    q.target_key = p.target_key
                stats.fuzzy_merged += 1
                continue

        kept.append(p)
        ki = len(kept) - 1
        for t in toks:
            index[(p.entity_type, t)].append(ki)

    logger.info("KG 7단계 퍼지: %d → %d (병합 %d)", len(protos), len(kept), stats.fuzzy_merged)
    return kept


def _dedupe_canonicals(items: list[CanonicalEntity]) -> list[CanonicalEntity]:
    """같은 canonical_id가 두 번 나오면 언급수를 합쳐 하나로 만든다."""
    by_id: dict[str, CanonicalEntity] = {}
    for e in items:
        cur = by_id.get(e.canonical_id)
        if cur is None:
            by_id[e.canonical_id] = e
            continue
        cur.mention_count += e.mention_count
        for a in e.aliases:
            if a not in cur.aliases:
                cur.aliases.append(a)
        if e.review_status == "REVIEW_REQUIRED":
            cur.review_status = "REVIEW_REQUIRED"
    return list(by_id.values())


def _write_in_batches(
    store: CandidateStore,
    canonicals: Sequence[CanonicalEntity],
    links: Sequence[tuple[str, str, str]],
    batch: int,
) -> None:
    for i in range(0, len(canonicals), batch):
        store.upsert_canonicals_bulk(canonicals[i : i + batch])
    for i in range(0, len(links), batch):
        store.link_doc_entities_bulk(links[i : i + batch])
    # 후보 행 전파는 마지막에 집합 연산으로 한 번에 — 행별 UPDATE는 20만 행에서 몇 시간이다.
    store.propagate_canonical_to_candidates()


def review_queue(store: CandidateStore, limit: int = 200) -> list[dict[str, object]]:
    """검토가 필요한 정규 엔티티 — 블롭 감시·모호 판정에 걸린 것들."""
    rows = store._conn.execute(  # noqa: SLF001
        "SELECT canonical_id, entity_type, canonical_name, target_key, mention_count,"
        " aliases_json FROM canonical_entities WHERE review_status='REVIEW_REQUIRED'"
        " ORDER BY mention_count DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def counts_by_type(store: CandidateStore) -> dict[str, int]:
    rows = store._conn.execute(  # noqa: SLF001
        "SELECT entity_type, COUNT(*) AS c FROM canonical_entities GROUP BY entity_type"
    ).fetchall()
    return {r["entity_type"]: r["c"] for r in rows}


__all__ = [
    "ConsolidateStats",
    "NormalizeStats",
    "consolidate_documents",
    "counts_by_type",
    "normalize_global",
    "review_queue",
]
