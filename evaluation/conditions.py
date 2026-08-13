"""
conditions.py
-------------
Defines the three ablation conditions described in the paper (Section IV.E):

    (a) LLM Only (no retrieval)
    (b) RAG with documentation only
    (c) RAG with documentation and historical failure logs

Each condition is materialised by constructing a ``DiagnosisAgent`` with the
appropriate ``mode``.  The condition identifiers are kept stable so that
downstream metric scripts (Update 5) and the dashboard (Update 6) can join
results across updates.
"""

from typing import Literal, TypedDict

from app.agent.react_agent import DiagnosisAgent, DiagnosisMode
from app.rag.store import VectorStore

DiagnosisCondition = Literal["llm_only", "rag_docs", "rag_full"]

ABLATION_CONDITIONS: tuple[DiagnosisCondition, ...] = (
    "llm_only",
    "rag_docs",
    "rag_full",
)

# Human-readable labels for tables / dashboards.
CONDITION_LABELS: dict[DiagnosisCondition, str] = {
    "llm_only": "(a) LLM Only (no retrieval)",
    "rag_docs": "(b) RAG with documentation only",
    "rag_full": "(c) RAG with documentation + history",
}


class ConditionSpec(TypedDict):
    condition: DiagnosisCondition
    label: str
    mode: DiagnosisMode
    requires_store: bool


CONDITION_SPECS: list[ConditionSpec] = [
    {
        "condition": "llm_only",
        "label": CONDITION_LABELS["llm_only"],
        "mode": "llm_only",
        "requires_store": False,
    },
    {
        "condition": "rag_docs",
        "label": CONDITION_LABELS["rag_docs"],
        "mode": "rag_docs",
        "requires_store": True,
    },
    {
        "condition": "rag_full",
        "label": CONDITION_LABELS["rag_full"],
        "mode": "rag_full",
        "requires_store": True,
    },
]


def build_agent_for_condition(
    condition: DiagnosisCondition,
    store: VectorStore | None = None,
) -> DiagnosisAgent:
    """Construct a ``DiagnosisAgent`` configured for *condition*.

    For ``llm_only`` the store may be ``None``; for the RAG conditions a
    non-``None`` ``VectorStore`` is required and will be validated by the
    agent constructor.
    """
    spec = next((s for s in CONDITION_SPECS if s["condition"] == condition), None)
    if spec is None:
        raise ValueError(
            f"Unknown condition {condition!r}; expected one of {ABLATION_CONDITIONS}."
        )

    if spec["requires_store"] and store is None:
        raise ValueError(f"Condition {condition!r} requires a VectorStore.")

    if spec["mode"] == "llm_only":
        return DiagnosisAgent(store=None, mode="llm_only")
    return DiagnosisAgent(store=store, mode=spec["mode"])
