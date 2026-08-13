"""
evaluation
----------
Experimental harness for the AI-powered CI/CD failure diagnosis system.

Package contents
----------------
* ``scenarios``  – the 15–20 injected failure scenarios with ground truth.
* ``conditions`` – the three ablation conditions (LLM-only, RAG-docs, RAG-full).
* ``runner``     – executes scenarios under each condition and persists raw
                   per-scenario results for downstream metric computation.
* ``metrics``    – computes diagnostic accuracy, remediation actionability,
                   mean time-to-diagnosis, NDCG, and per-category tables
                   from ``raw_results.json``.
"""

from evaluation.conditions import (
    ABLATION_CONDITIONS,
    build_agent_for_condition,
    DiagnosisCondition,
)

from evaluation.metrics import (
    compute_metrics,
    load_raw_results,
    save_report,
    print_summary,
    EvaluationReport,
    ScenarioMetrics,
    ConditionSummary,
    NDCGResult,
)

__all__ = [
    "ABLATION_CONDITIONS",
    "build_agent_for_condition",
    "DiagnosisCondition",
    "compute_metrics",
    "load_raw_results",
    "save_report",
    "print_summary",
    "EvaluationReport",
    "ScenarioMetrics",
    "ConditionSummary",
    "NDCGResult",
]
