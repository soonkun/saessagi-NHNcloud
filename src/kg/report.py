# src/kg/report.py
"""M_23 10단계 — 관찰 리포트 (스펙 §9.1).

**이것은 품질 증명이 아니다.** 스펙 §9는 골든셋 평가로 Entity Precision과 False Merge
Rate를 재라고 했지만, 골든셋은 사람이 라벨링해야 만들어지는 물건이고 아직 없다. 없는
평가를 통과한 척하는 것이 이 프로젝트에서 가장 하면 안 되는 일이라(CLAUDE.md 절대 규칙),
여기서는 **관찰값만** 낸다.

관찰값으로도 알 수 있는 것이 있다.
- 단일 문서 전용 엔티티 비율이 높으면 그래프가 '별들의 숲'이라는 뜻이다(CR-34 재현).
- 블롭 감시에 걸린 그룹이 있으면 병합 규칙이 어딘가에서 과하게 붙고 있다는 뜻이다(CR-36).
- df 최상위가 전부 행정 상용구면 상용구 임계값이 잘못 잡힌 것이다.

골든셋 구축은 별도 과제로 남는다. 그 전까지 "정확도 N%"라고 말할 근거는 없다.
"""

from __future__ import annotations

import logging
from typing import Any

from .candidates import CandidateStore
from .config import KnowledgeGraphConfig

logger = logging.getLogger(__name__)


def build_report(store: CandidateStore, config: KnowledgeGraphConfig) -> dict[str, Any]:
    """구축 결과를 관찰한다. 판정하지 않는다."""
    conn = store._conn  # noqa: SLF001

    def one(sql: str, *a: Any) -> Any:
        row = conn.execute(sql, a).fetchone()
        return row[0] if row else 0

    def rows(sql: str, *a: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(sql, a).fetchall()]

    total_canon = int(one("SELECT COUNT(*) FROM canonical_entities"))
    singletons = int(one("SELECT COUNT(*) FROM canonical_entities WHERE document_frequency <= 1"))
    shared = total_canon - singletons

    report: dict[str, Any] = {
        "note": (
            "관찰 리포트입니다. 골든셋 평가가 아니며 정확도·오병합률을 주장하지 않습니다 "
            "(스펙 §9.1)."
        ),
        "규모": {
            "documents": int(one("SELECT COUNT(*) FROM documents")),
            "entity_candidates": int(one("SELECT COUNT(*) FROM entity_candidates")),
            "doc_entities": int(one("SELECT COUNT(*) FROM doc_entities")),
            "canonical_entities": total_canon,
            "relation_candidates": int(one("SELECT COUNT(*) FROM relation_candidates")),
        },
        "연결성": _connectivity(one, total_canon, singletons, shared),
        "잡음": {
            "상용구_임계값_df": config.graph.boilerplate_document_frequency,
            "상용구_표시": int(
                one("SELECT COUNT(*) FROM canonical_entities WHERE is_boilerplate=1")
            ),
            "df_최상위_20": rows(
                "SELECT canonical_name, entity_type, document_frequency AS df, is_boilerplate"
                " FROM canonical_entities ORDER BY document_frequency DESC LIMIT 20"
            ),
        },
        "병합_감시": {
            "검토필요_엔티티": int(
                one("SELECT COUNT(*) FROM canonical_entities WHERE review_status='REVIEW_REQUIRED'")
            ),
            "별칭_최다_15": rows(
                "SELECT canonical_name, entity_type, mention_count,"
                " json_array_length(aliases_json) AS alias_count"
                " FROM canonical_entities ORDER BY alias_count DESC LIMIT 15"
            ),
        },
        "계획_실적_축": {
            "note": (
                "statement_status가 97.3% UNCERTAIN이라 문서유형에서 유도했습니다. "
                "완결보고서 안에서 계획과 실적은 가르지 못합니다 (스펙 §5.3)."
            ),
            "분포": rows(
                "SELECT statement_status, status_source, COUNT(*) AS c"
                " FROM relation_candidates WHERE source_kind='PROJECT'"
                " GROUP BY statement_status, status_source ORDER BY c DESC"
            ),
        },
        "이름_분할": {
            "note": (
                "이름·유형이 같은데 target_key가 달라 갈라진 노드입니다. 병합 금지 규칙 R2가 "
                "설계대로 동작한 결과지만, APPLIED_TO 엣지가 생긴 지금은 과할 수 있습니다 — "
                "'특성평가'를 대상별로 47개로 쪼개는 대신 노드 하나 + APPLIED_TO 47개가 "
                "나을 수 있습니다. 튜닝 판단이 필요한 지점이라 관찰값으로 냅니다 "
                "(normalization.prevent_different_target_merge)."
            ),
            "분할된_이름_그룹": int(
                one(
                    "SELECT COUNT(*) FROM (SELECT canonical_name, entity_type"
                    " FROM canonical_entities GROUP BY canonical_name, entity_type"
                    " HAVING COUNT(*) > 1)"
                )
            ),
            "분할로_생긴_노드": int(
                one(
                    "SELECT COALESCE(SUM(c),0) FROM (SELECT COUNT(*) AS c"
                    " FROM canonical_entities GROUP BY canonical_name, entity_type"
                    " HAVING COUNT(*) > 1)"
                )
            ),
            "가장_많이_갈린_10": rows(
                "SELECT canonical_name, entity_type, COUNT(*) AS 분할수"
                " FROM canonical_entities GROUP BY canonical_name, entity_type"
                " HAVING COUNT(*) > 1 ORDER BY 분할수 DESC LIMIT 10"
            ),
        },
        "유형별": rows(
            "SELECT entity_type, COUNT(*) AS entities,"
            " SUM(CASE WHEN document_frequency >= 2 THEN 1 ELSE 0 END) AS shared,"
            " MAX(document_frequency) AS max_df"
            " FROM canonical_entities GROUP BY entity_type ORDER BY entities DESC"
        ),
        "과제": {
            "총": int(one("SELECT COUNT(DISTINCT project_id) FROM documents WHERE project_id!=''")),
            "출처별": rows(
                "SELECT project_id_source, COUNT(DISTINCT project_id) AS c FROM documents"
                " WHERE project_id != '' GROUP BY project_id_source"
            ),
            "다문서_과제": int(
                one(
                    "SELECT COUNT(*) FROM (SELECT project_id FROM documents"
                    " WHERE project_id != '' GROUP BY project_id HAVING COUNT(*) > 1)"
                )
            ),
            "note": (
                "RFP는 project_no·rfp_no가 둘 다 비어 있어 계획서↔완결보고서 연결이 "
                "현재 데이터로는 거의 불가능합니다 (스펙 §4.1-D)."
            ),
        },
    }
    return report


def _connectivity(one: Any, total_canon: int, singletons: int, shared: int) -> dict[str, Any]:
    """연결성 지표.

    **단일문서 비율만 보면 안 된다.** 엔티티의 91%가 한 문서에만 나오는 것은 이 코퍼스의
    성질이고(긴 서술형 이름), 병합으로 고쳐지지 않는다(실측 0.5%). 우리가 한 일은 그
    엔티티들을 **대상 허브에 매달아** 문서 사이를 잇는 것이다. 그래서 진짜 지표는
    "몇 개가 그래프에 실제로 이어졌는가"다.

    이어진 것으로 치는 조건:
    - df >= 2 (여러 문서가 직접 공유) — 그 자체로 문서 간 연결
    - 또는 APPLIED_TO로 공유 대상 허브(df>=2)에 매달림 — 허브를 거쳐 연결
    """
    linked_via_hub = int(
        one(
            "SELECT COUNT(DISTINCT rc.source_canonical_id) FROM relation_candidates rc"
            " JOIN canonical_entities ce ON ce.canonical_id = rc.target_canonical_id"
            " WHERE rc.relation_type='APPLIED_TO' AND ce.document_frequency >= 2"
        )
    )
    isolated = int(
        one(
            "SELECT COUNT(*) FROM canonical_entities ce WHERE ce.document_frequency <= 1"
            " AND NOT EXISTS (SELECT 1 FROM relation_candidates rc"
            "   JOIN canonical_entities t ON t.canonical_id = rc.target_canonical_id"
            "   WHERE rc.relation_type='APPLIED_TO' AND rc.source_canonical_id = ce.canonical_id"
            "     AND t.document_frequency >= 2)"
        )
    )
    connected = total_canon - isolated
    return {
        "단일문서_전용": singletons,
        "단일문서_전용_비율": round(singletons / total_canon, 4) if total_canon else 0.0,
        "다문서_공유": shared,
        "다문서_공유_비율": round(shared / total_canon, 4) if total_canon else 0.0,
        "허브경유_연결": linked_via_hub,
        "그래프에_연결됨": connected,
        "그래프_연결_비율": round(connected / total_canon, 4) if total_canon else 0.0,
        "고립": isolated,
        "고립_비율": round(isolated / total_canon, 4) if total_canon else 0.0,
        "target_key_승격_엔티티": int(
            one("SELECT COUNT(*) FROM canonical_entities WHERE from_target_key=1")
        ),
        "APPLIED_TO_엣지": int(
            one("SELECT COUNT(*) FROM relation_candidates WHERE relation_type='APPLIED_TO'")
        ),
        "SHARES_ENTITY_엣지": int(
            one("SELECT COUNT(*) FROM relation_candidates WHERE relation_type='SHARES_ENTITY'")
        ),
    }


def connectivity_warnings(report: dict[str, Any]) -> list[str]:
    """관찰값에서 눈에 띄는 것을 말로 바꾼다. 자동 판정이 아니라 주의 환기다."""
    out: list[str] = []
    conn_block = report.get("연결성", {})
    # 단일문서 비율이 아니라 **고립 비율**로 판정한다 — 앞의 것은 이 코퍼스에서 항상 높다.
    isolated = conn_block.get("고립_비율", 0.0)
    if isolated > 0.60:
        out.append(
            f"어떤 문서와도 이어지지 않은 엔티티가 {isolated:.1%}입니다. "
            "그래프가 '별들의 숲'에 가깝습니다 — target_key 승격과 APPLIED_TO가 "
            "실제로 걸렸는지 확인하세요 (CR-34 재현 위험)."
        )
    if conn_block.get("APPLIED_TO_엣지", 0) == 0:
        out.append("APPLIED_TO 엣지가 0건입니다. target_key 승격이 꺼져 있거나 실패했습니다.")
    watch = report.get("병합_감시", {})
    if watch.get("검토필요_엔티티", 0) > report.get("규모", {}).get("canonical_entities", 1) * 0.1:
        out.append("검토 필요 엔티티가 전체의 10%를 넘습니다 — 병합 규칙을 다시 보세요.")
    return out


__all__ = ["build_report", "connectivity_warnings"]
