# src/deep_research/__init__.py
"""M_20 DeepResearch — GraphRAG 기반 심층 자료 검토·보고서 생성 (CR-20, CR-62)."""

from .service import DeepResearchService, ResearchProfile
from .store import InstructionVersion, ResearchProject, ResearchProjectStore, Turn

__all__ = [
    "DeepResearchService",
    "InstructionVersion",
    "ResearchProfile",
    "ResearchProject",
    "ResearchProjectStore",
    "Turn",
]
