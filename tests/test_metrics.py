"""
test_metrics.py
---------------
Tests for the Update 5 metrics computation module.

Uses a small synthetic raw_results.json to verify:
- accuracy, actionability, time-to-diagnosis calculations
- per-category breakdowns
- NDCG computation
- confusion matrix construction
- report serialization
"""

import json
import tempfile
from pathlib import Path

import pytest

from evaluation.metrics import (
    compute_metrics,
    load_raw_results,
    save_report,
    report_to_dict,
    ScenarioMetrics,
    ConditionSummary,
    NDCGResult,
    EvaluationReport,
    print_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_raw_results() -> dict:
    """Minimal raw_results with 2 scenarios x 3 conditions = 6 records."""
    return {
        "generated_at_utc": "2026-01-01T00:00:00+00:00",
        "config": {
            "llm_model": "gpt-test",
            "embedding_model": "all-MiniLM-L6-v2",
            "top_k": 5,
            "chunk_size": 512,
            "chunk_overlap": 64,
        },
        "num_scenarios": 2,
        "num_conditions": 3,
        "records": [
            # DEP-001, llm_only - correct category, partial actionability
            {
                "scenario_id": "DEP-001",
                "condition": "llm_only",
                "condition_label": "(a) LLM Only (no retrieval)",
                "ground_truth": {
                    "category": "dependency_error",
                    "expected_root_cause": "urllib3/requests version conflict",
                    "remediation_keywords": ["urllib3", "requests", "pin", "requirements.txt"],
                },
                "diagnosis": {
                    "failure_category": "dependency_error",
                    "root_cause": "version conflict between urllib3 and requests",
                    "confidence_score": 0.85,
                    "recommended_fix": "pin urllib3 and requests in requirements.txt",
                    "retrieved_references": [],
                    "diagnosis_mode": "llm_only",
                },
                "elapsed_seconds": 1.2,
                "timestamp_utc": "2026-01-01T00:00:01+00:00",
            },
            # DEP-001, rag_docs - correct, higher confidence, references
            {
                "scenario_id": "DEP-001",
                "condition": "rag_docs",
                "condition_label": "(b) RAG with documentation only",
                "ground_truth": {
                    "category": "dependency_error",
                    "expected_root_cause": "urllib3/requests version conflict",
                    "remediation_keywords": ["urllib3", "requests", "pin", "requirements.txt"],
                },
                "diagnosis": {
                    "failure_category": "dependency_error",
                    "root_cause": "urllib3 2.x conflicts with requests 2.31.0",
                    "confidence_score": 0.92,
                    "recommended_fix": "pin urllib3<2 and requests in requirements.txt then reinstall",
                    "retrieved_references": ["dependency_docs", "github_actions_docs"],
                    "diagnosis_mode": "rag_docs",
                },
                "elapsed_seconds": 1.8,
                "timestamp_utc": "2026-01-01T00:00:03+00:00",
            },
            # DEP-001, rag_full - correct
            {
                "scenario_id": "DEP-001",
                "condition": "rag_full",
                "condition_label": "(c) RAG with documentation + history",
                "ground_truth": {
                    "category": "dependency_error",
                    "expected_root_cause": "urllib3/requests version conflict",
                    "remediation_keywords": ["urllib3", "requests", "pin", "requirements.txt"],
                },
                "diagnosis": {
                    "failure_category": "dependency_error",
                    "root_cause": "urllib3/requests conflict resolved by pinning",
                    "confidence_score": 0.95,
                    "recommended_fix": "add urllib3<2.0, requests==2.31.0 to requirements.txt",
                    "retrieved_references": ["dependency_docs", "feedback_loop", "github_actions_docs"],
                    "diagnosis_mode": "rag_full",
                },
                "elapsed_seconds": 2.1,
                "timestamp_utc": "2026-01-01T00:00:05+00:00",
            },
            # TST-001, llm_only - wrong category (test_failure -> unknown)
            {
                "scenario_id": "TST-001",
                "condition": "llm_only",
                "condition_label": "(a) LLM Only (no retrieval)",
                "ground_truth": {
                    "category": "test_failure",
                    "expected_root_cause": "floating point drift in pricing calc",
                    "remediation_keywords": ["float", "round", "assert", "pytest.approx", "decimal"],
                },
                "diagnosis": {
                    "failure_category": "unknown",
                    "root_cause": "unclear",
                    "confidence_score": 0.3,
                    "recommended_fix": "inspect logs manually",
                    "retrieved_references": [],
                    "diagnosis_mode": "llm_only",
                },
                "elapsed_seconds": 0.9,
                "timestamp_utc": "2026-01-01T00:00:07+00:00",
            },
            # TST-001, rag_docs - correct category, low actionability
            {
                "scenario_id": "TST-001",
                "condition": "rag_docs",
                "condition_label": "(b) RAG with documentation only",
                "ground_truth": {
                    "category": "test_failure",
                    "expected_root_cause": "floating point drift in pricing calc",
                    "remediation_keywords": ["float", "round", "assert", "pytest.approx", "decimal"],
                },
                "diagnosis": {
                    "failure_category": "test_failure",
                    "root_cause": "assertion error on float comparison",
                    "confidence_score": 0.65,
                    "recommended_fix": "update test to use approximate comparison",
                    "retrieved_references": ["test_docs"],
                    "diagnosis_mode": "rag_docs",
                },
                "elapsed_seconds": 1.5,
                "timestamp_utc": "2026-01-01T00:00:09+00:00",
            },
            # TST-001, rag_full - correct category, good actionability
            {
                "scenario_id": "TST-001",
                "condition": "rag_full",
                "condition_label": "(c) RAG with documentation + history",
                "ground_truth": {
                    "category": "test_failure",
                    "expected_root_cause": "floating point drift in pricing calc",
                    "remediation_keywords": ["float", "round", "assert", "pytest.approx", "decimal"],
                },
                "diagnosis": {
                    "failure_category": "test_failure",
                    "root_cause": "float drift causes assert 19.99 == 19.989999 to fail",
                    "confidence_score": 0.88,
                    "recommended_fix": "use pytest.approx or decimal for monetary comparisons",
                    "retrieved_references": ["test_docs", "feedback_loop"],
                    "diagnosis_mode": "rag_full",
                },
                "elapsed_seconds": 1.9,
                "timestamp_utc": "2026-01-01T00:00:11+00:00",
            },
        ],
    }


@pytest.fixture
def raw_results_file(synthetic_raw_results, tmp_path):
    path = tmp_path / "raw_results.json"
    path.write_text(json.dumps(synthetic_raw_results))
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_raw_results(raw_results_file):
    raw = load_raw_results(raw_results_file)
    assert raw["num_scenarios"] == 2
    assert raw["num_conditions"] == 3
    assert len(raw["records"]) == 6


def test_compute_metrics_basic(synthetic_raw_results):
    report = compute_metrics(synthetic_raw_results)

    # 2 scenarios x 3 conditions
    assert len(report.scenario_metrics) == 6
    assert len(report.condition_summaries) == 3

    # Check condition summaries
    cond_by_name = {cs.condition: cs for cs in report.condition_summaries}

    # llm_only: 1/2 correct (DEP-001 correct, TST-001 wrong)
    llm = cond_by_name["llm_only"]
    assert llm.n_scenarios == 2
    assert abs(llm.accuracy - 0.5) < 1e-6

    # rag_docs: 2/2 correct
    rag_docs = cond_by_name["rag_docs"]
    assert rag_docs.n_scenarios == 2
    assert abs(rag_docs.accuracy - 1.0) < 1e-6

    # rag_full: 2/2 correct
    rag_full = cond_by_name["rag_full"]
    assert rag_full.n_scenarios == 2
    assert abs(rag_full.accuracy - 1.0) < 1e-6


def test_per_category_breakdown(synthetic_raw_results):
    report = compute_metrics(synthetic_raw_results)

    rag_docs = next(cs for cs in report.condition_summaries if cs.condition == "rag_docs")
    # dependency_error: 1 scenario, correct -> acc=1.0
    dep = rag_docs.per_category["dependency_error"]
    assert dep["count"] == 1
    assert abs(dep["accuracy"] - 1.0) < 1e-6

    # test_failure: 1 scenario, correct -> acc=1.0
    tst = rag_docs.per_category["test_failure"]
    assert tst["count"] == 1
    assert abs(tst["accuracy"] - 1.0) < 1e-6


def test_actionability_scoring(synthetic_raw_results):
    report = compute_metrics(synthetic_raw_results)

    # DEP-001 llm_only: keywords ["urllib3", "requests", "pin", "requirements.txt"]
    # fix: "pin urllib3 and requests in requirements.txt" -> 4/4 = 1.0
    dep_llm = next(
        sm for sm in report.scenario_metrics
        if sm.scenario_id == "DEP-001" and sm.condition == "llm_only"
    )
    assert abs(dep_llm.actionability - 1.0) < 1e-6

    # TST-001 rag_docs: keywords ["float", "round", "assert", "pytest.approx", "decimal"]
    # fix: "update test to use approximate comparison" -> no required keyword matches -> 0/5 = 0.0
    tst_rag_docs = next(
        sm for sm in report.scenario_metrics
        if sm.scenario_id == "TST-001" and sm.condition == "rag_docs"
    )
    assert abs(tst_rag_docs.actionability - 0.0) < 1e-6


def test_ndcg_computation(synthetic_raw_results):
    report = compute_metrics(synthetic_raw_results)

    # Should have NDCG results for all records with retrieved_references
    # llm_only has empty refs -> ndcg = 0.0
    # rag_docs and rag_full have refs
    assert len(report.ndcg_results) == 6

    # Check rag_docs DEP-001: refs ["dependency_docs", "github_actions_docs"]
    # relevant for dependency_error: {"dependency_docs", "github_actions_docs"}
    # -> perfect match -> NDCG = 1.0
    dep_rag_docs = next(
        nr for nr in report.ndcg_results
        if nr.scenario_id == "DEP-001" and nr.condition == "rag_docs"
    )
    assert abs(dep_rag_docs.ndcg_at_k - 1.0) < 1e-6

    # TST-001 rag_docs: refs ["test_docs"]
    # relevant for test_failure: {"test_docs", "github_actions_docs"}
    # -> test_docs is relevant, rank 1 -> DCG = 1/log2(2) = 1, IDCG = 1 -> NDCG = 1.0
    tst_rag_docs = next(
        nr for nr in report.ndcg_results
        if nr.scenario_id == "TST-001" and nr.condition == "rag_docs"
    )
    assert abs(tst_rag_docs.ndcg_at_k - 1.0) < 1e-6


def test_confusion_matrix(synthetic_raw_results):
    report = compute_metrics(synthetic_raw_results)

    # llm_only: DEP-001 true=dependency_error pred=dependency_error
    #           TST-001 true=test_failure pred=unknown
    cm = report.confusion_matrices["llm_only"]
    assert cm["dependency_error"]["dependency_error"] == 1
    assert cm["test_failure"]["unknown"] == 1


def test_report_serialization(synthetic_raw_results, tmp_path):
    report = compute_metrics(synthetic_raw_results)
    out_path = tmp_path / "metrics.json"
    save_report(report, out_path)

    assert out_path.exists()
    loaded = json.loads(out_path.read_text())
    assert "condition_summaries" in loaded
    assert "scenario_metrics" in loaded
    assert "ndcg_results" in loaded
    assert "confusion_matrices" in loaded

    # Round-trip via dict
    d = report_to_dict(report)
    assert isinstance(d["condition_summaries"], list)
    assert len(d["condition_summaries"]) == 3


def test_print_summary_runs(synthetic_raw_results, capsys):
    report = compute_metrics(synthetic_raw_results)
    print_summary(report)
    captured = capsys.readouterr()
    assert "EVALUATION METRICS SUMMARY" in captured.out
    assert "llm_only" in captured.out
    assert "Confusion matrix" in captured.out


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_retrieved_references_ndcg_zero():
    """Scenario with no retrieved references should yield NDCG 0."""
    raw = {
        "generated_at_utc": "2026-01-01T00:00:00+00:00",
        "config": {},
        "num_scenarios": 1,
        "num_conditions": 1,
        "records": [{
            "scenario_id": "DEP-001",
            "condition": "llm_only",
            "condition_label": "(a) LLM Only",
            "ground_truth": {
                "category": "dependency_error",
                "expected_root_cause": "conflict",
                "remediation_keywords": ["pin"],
            },
            "diagnosis": {
                "failure_category": "dependency_error",
                "root_cause": "conflict",
                "confidence_score": 0.5,
                "recommended_fix": "pin it",
                "retrieved_references": [],
                "diagnosis_mode": "llm_only",
            },
            "elapsed_seconds": 1.0,
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
        }],
    }
    report = compute_metrics(raw)
    nr = next(nr for nr in report.ndcg_results if nr.scenario_id == "DEP-001")
    assert nr.ndcg_at_k == 0.0


def test_unknown_category_ndcg_zero():
    """Category 'unknown' has no relevant sources -> NDCG 0."""
    raw = {
        "generated_at_utc": "2026-01-01T00:00:00+00:00",
        "config": {},
        "num_scenarios": 1,
        "num_conditions": 1,
        "records": [{
            "scenario_id": "UNK-001",
            "condition": "rag_full",
            "condition_label": "(c) RAG Full",
            "ground_truth": {
                "category": "unknown",
                "expected_root_cause": "mystery",
                "remediation_keywords": [],
            },
            "diagnosis": {
                "failure_category": "unknown",
                "root_cause": "mystery",
                "confidence_score": 0.1,
                "recommended_fix": "manual",
                "retrieved_references": ["dependency_docs"],
                "diagnosis_mode": "rag_full",
            },
            "elapsed_seconds": 1.0,
            "timestamp_utc": "2026-01-01T00:00:00+00:00",
        }],
    }
    report = compute_metrics(raw)
    nr = next(nr for nr in report.ndcg_results if nr.scenario_id == "UNK-001")
    assert nr.ndcg_at_k == 0.0