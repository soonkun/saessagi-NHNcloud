# tests/kg/test_extract.py
"""M_23 추출 실행기 테스트 (지침서 11.2, 23장 / 스펙 §3).

실제 LLM 없이 대역으로 확인하는 것:
- 실패한 청크 하나가 **배치 전체를 멈추지 않는다**
- 재시도 후에도 실패하면 그 청크만 FAILED로 남는다
- 같은 문서를 다시 돌려도 후보가 중복되지 않는다
- 중단 요청이 청크 경계에서 안전하게 먹는다
- 과제번호가 문서 메타에 실린다 (계획서–완결보고서 연결축)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kg.candidates import CandidateStore
from kg.config import KnowledgeGraphConfig
from kg.documents import FolderInfo
from kg.extract import ExtractionRunner
from kg.identity import extract_identity, project_key

SOURCE_HEAD = (
    "농촌진흥청 연구개발과제 완결보고서\n"
    "과제번호: PJ013094\n"
    "과제명: 사과 유전체 육종시스템 개발\n"
    "연구기간 2021~2025"
)
SOURCE_BODY = (
    "3. 연구수행 내용 및 결과\n"
    "본 연구에서는 SWAT+ 모형을 개선하여 사과 과수원의 수질 예측 정확도를 향상시켰다. "
    "현장 실증을 3개소에서 수행하였으며 예측 오차는 12% 감소하였다."
)


class FakeVectorStore:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def get_chunks_by_doc_id(self, doc_id: str, limit: int = 30) -> list[dict[str, Any]]:
        return [r for r in self._rows if r["doc_id"] == doc_id][:limit]


def _rows(doc_id: str = "D1") -> list[dict[str, Any]]:
    rows = [
        {
            "doc_id": doc_id,
            "doc_name": "완결보고서.pdf",
            "category": "F1",
            "chunk_id": f"{doc_id}#0",
            "page": 1,
            "text": SOURCE_HEAD,
        },
        {
            "doc_id": doc_id,
            "doc_name": "완결보고서.pdf",
            "category": "F1",
            "chunk_id": f"{doc_id}#1",
            "page": 12,
            "text": SOURCE_BODY,
        },
    ]
    return rows


def _entity(name: str = "SWAT+ 모형", **kw: Any) -> dict[str, Any]:
    base = {
        "temp_id": "E1",
        "type": "TECHNOLOGY",
        "name": name,
        "canonical_name_candidate": name,
        "status": "ACTUAL",
        "current_project_relevance": "DIRECT",
        "target_terms": ["사과"],
        "evidence": "본 연구에서는 SWAT+ 모형을 개선하여 사과 과수원의 수질 예측 정확도를 향상시켰다.",
        "confidence": 0.93,
    }
    base.update(kw)
    return base


class ScriptedAgent:
    """호출 순서대로 미리 정한 응답이나 예외를 돌려준다."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = 0
        self.prompts: list[str] = []

    async def __call__(
        self, system_prompt: str, user_prompt: str, json_schema: dict[str, Any], **kw: Any
    ) -> dict[str, Any]:
        self.calls += 1
        self.prompts.append(user_prompt)
        item = self.script.pop(0) if self.script else {"entities": []}
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def store(tmp_path: Path) -> CandidateStore:
    return CandidateStore(tmp_path / "kg.db")


@pytest.fixture
def config() -> KnowledgeGraphConfig:
    cfg = KnowledgeGraphConfig()
    cfg.extraction.chunks_per_document = 2
    cfg.extraction.max_retries = 2
    cfg.jobs.yield_to_conversation = False
    return cfg


def runner(agent: Any, store: CandidateStore, cfg: KnowledgeGraphConfig) -> ExtractionRunner:
    return ExtractionRunner(
        complete_json=agent,
        store=store,
        vector_store=FakeVectorStore(_rows()),
        config=cfg,
        folder_index={"F1": FolderInfo("F1", "2025완결보고서", "FINAL_REPORT", 2025)},
        model_name="fake-model",
    )


class TestIdentity:
    def test_reads_project_number_and_title(self) -> None:
        ident = extract_identity(SOURCE_HEAD)
        assert ident.project_no == "PJ013094"
        assert ident.title == "사과 유전체 육종시스템 개발"
        assert (ident.start_year, ident.end_year) == (2021, 2025)

    def test_project_key_groups_plan_and_report(self) -> None:
        """같은 과제번호면 계획서와 완결보고서가 같은 Project로 묶인다."""
        a = extract_identity("과제번호: PJ013094\n과제명: 사과 육종")
        b = extract_identity("과제번호 : PJ 013094\n과제명: 사과 육종 완결")
        assert project_key(a, "docA") == project_key(b, "docB")

    def test_no_number_falls_back_to_document(self) -> None:
        """번호가 없으면 문서별로 둔다 — 제목 유사도로 합치지 않는다."""
        ident = extract_identity("표지\n연구보고서")
        assert project_key(ident, "docA") != project_key(ident, "docB")


@pytest.mark.asyncio
class TestExtraction:
    async def test_happy_path(self, store: CandidateStore, config: KnowledgeGraphConfig) -> None:
        agent = ScriptedAgent([{"entities": [_entity()]}, {"entities": [_entity("현장 실증")]}])
        out = await runner(agent, store, config).extract_document("D1")

        assert out.state == "COMPLETED"
        assert out.accepted >= 1
        doc = store.get_document("D1")
        assert doc is not None
        assert doc.project_no == "PJ013094"
        assert doc.document_type == "FINAL_REPORT"
        cands = store.candidates_for_document("D1")
        assert cands and cands[0].target_key  # 병합 판정 입력이 채워져 있어야 한다
        assert cands[0].extractor_model == "fake-model"

    async def test_one_failing_chunk_does_not_stop_document(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        """지침서 11.2 — 전체 배치를 중단하지 않는다."""
        agent = ScriptedAgent(
            [
                RuntimeError("timeout"),
                RuntimeError("timeout"),
                RuntimeError("timeout"),
                {"entities": [_entity()]},
            ]
        )
        out = await runner(agent, store, config).extract_document("D1")
        assert out.state == "PARTIAL_FAILED"
        assert out.failed_chunks == 1
        assert out.accepted == 1  # 나머지 청크는 정상 처리

    async def test_retry_then_success(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        agent = ScriptedAgent(
            [RuntimeError("깨진 JSON"), {"entities": [_entity()]}, {"entities": []}]
        )
        out = await runner(agent, store, config).extract_document("D1")
        assert out.state == "COMPLETED"
        assert out.chunks[0].attempts == 2

    async def test_last_attempt_uses_short_prompt(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        """3차 시도는 축약 프롬프트 — 출력이 길어 잘리는 경우 대비."""
        agent = ScriptedAgent(
            [RuntimeError("x"), RuntimeError("x"), {"entities": []}, {"entities": []}]
        )
        await runner(agent, store, config).extract_document("D1")
        assert len(agent.prompts[2]) < len(agent.prompts[0])

    async def test_rerun_does_not_duplicate(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        """같은 문서를 두 번 돌려도 후보가 늘지 않아야 한다 (지침서 4.6)."""
        for _ in range(2):
            agent = ScriptedAgent([{"entities": [_entity()]}, {"entities": [_entity()]}])
            await runner(agent, store, config).extract_document("D1")
        assert len(store.candidates_for_document("D1")) == 1

    async def test_invalid_entities_are_rejected_not_stored(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        """근거가 원문에 없으면 저장되지 않는다."""
        agent = ScriptedAgent(
            [
                {"entities": [_entity(evidence="딥러닝 병해충 모델로 95% 정확도를 달성하였다.")]},
                {"entities": []},
            ]
        )
        out = await runner(agent, store, config).extract_document("D1")
        assert out.accepted == 0 and out.rejected == 1
        assert store.candidates_for_document("D1") == []

    async def test_cancel_stops_at_chunk_boundary(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        agent = ScriptedAgent([{"entities": [_entity()]}, {"entities": [_entity()]}])
        r = runner(agent, store, config)
        r.cancel()
        out = await r.extract_document("D1")
        assert out.state == "CANCELLED"
        assert agent.calls == 0

    async def test_missing_document(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        agent = ScriptedAgent([])
        out = await runner(agent, store, config).extract_document("없는문서")
        assert out.state == "FAILED"

    async def test_malformed_response_shape(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        """entities가 리스트가 아니면 재시도하고, 끝내 안 되면 FAILED."""
        agent = ScriptedAgent([{"entities": "이건 문자열"}] * 6)
        out = await runner(agent, store, config).extract_document("D1")
        assert out.failed_chunks == 2 and out.state == "FAILED"

    async def test_multiple_documents_continue_after_error(
        self, store: CandidateStore, config: KnowledgeGraphConfig
    ) -> None:
        agent = ScriptedAgent([{"entities": [_entity()]}] * 4)
        r = runner(agent, store, config)
        outs = await r.extract_documents(["없는문서", "D1"])
        assert [o.state for o in outs] == ["FAILED", "COMPLETED"]
