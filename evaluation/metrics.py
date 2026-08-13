"""
metrics.py
----------
Metric computation for the CI/CD failure-diagnosis evaluation.

Consumes the ``raw_results.json`` produced by ``evaluation.runner`` and computes:
* Diagnostic accuracy (category match)
* Remediation actionability (keyword coverage in recommended_fix)
* Mean time-to-diagnosis
* Per-category accuracy tables across conditions
* NDCG for retrieval quality (where retrieval data is available)

The module is designed to be imported or run as a CLI:

    python -m evaluation.metrics --input evaluation_results/raw_results.json \
                                   --output evaluation_results/metrics.json
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import numpy as np

from evaluation.scenarios import SCENARIOS, FailureScenario

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes for structured results
# ---------------------------------------------------------------------------


@dataclass
class ScenarioMetrics:
    """Per-scenario metric breakdown."""
    scenario_id: str
    condition: str
    category_correct: bool
    actionability_score: float  # 0.0–1.0 keyword coverage
    elapsed_seconds: float
    predicted_category: str
    expected_category: str
    confidence_score: float

    @property
    def actionability(self) -> float:
        """Backward-compatible alias used by earlier evaluation consumers."""
        return self.actionability_score


@dataclass
class ConditionSummary:
    """Aggregated metrics for one ablation condition."""
    condition: str
    condition_label: str
    n_scenarios: int
    accuracy: float
    actionability_mean: float
    time_to_diagnosis_mean: float
    time_to_diagnosis_std: float
    confidence_mean: float
    per_category: dict[str, dict[str, float]]  # category -> {accuracy, actionability, count}


@dataclass
class NDCGResult:
    """NDCG score for a single query (scenario)."""
    scenario_id: str
    condition: str
    ndcg_at_k: float
    k: int
    retrieved_sources: list[str]
    relevant_sources: list[str]


@dataclass
class EvaluationReport:
    """Full evaluation report."""
    generated_at_utc: str
    config: dict[str, Any]
    condition_summaries: list[ConditionSummary]
    scenario_metrics: list[ScenarioMetrics]
    ndcg_results: list[NDCGResult]
    confusion_matrices: dict[str, dict[str, dict[str, int]]]  # condition -> {true -> {pred -> count}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CATEGORIES = (
    "dependency_error",
    "docker_failure",
    "test_failure",
    "iac_misconfiguration",
    "unknown",
)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation-ish for keyword matching."""
    return text.lower()


def _keyword_coverage(recommended_fix: str, keywords: list[str]) -> float:
    """Fraction of ground-truth remediation keywords present in the fix (case-insensitive)."""
    if not keywords:
        return 1.0
    fix_norm = _normalize(recommended_fix)
    hits = sum(1 for kw in keywords if _normalize(kw) in fix_norm)
    return hits / len(keywords)


def _category_match(pred: str, expected: str) -> bool:
    return pred.strip().lower() == expected.strip().lower()


def _confusion_matrix(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Build confusion matrix: true_category -> {pred_category -> count}."""
    cm: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        true_cat = r["ground_truth"]["category"]
        pred_cat = r["diagnosis"].get("failure_category", "unknown")
        cm[true_cat][pred_cat] += 1
    # Ensure all categories appear
    for cat in _CATEGORIES:
        cm.setdefault(cat, {})
        for c2 in _CATEGORIES:
            cm[cat].setdefault(c2, 0)
    return {k: dict(v) for k, v in cm.items()}


# ---------------------------------------------------------------------------
# NDCG computation
# ---------------------------------------------------------------------------

def _dcg(relevances: list[float]) -> float:
    """Discounted Cumulative Gain."""
    return sum(rel / np.log2(i + 2) for i, rel in enumerate(relevances))


def _ndcg_at_k(retrieved: list[dict[str, Any]], relevant_sources: set[str], k: int) -> float:
    """
    Compute NDCG@k for a single query.

    retrieved: list of dicts with at least 'metadata' containing 'source'.
    relevant_sources: set of source names considered relevant for this scenario.
    """
    if not retrieved:
        return 0.0

    # Binary relevance: 1 if source in relevant_sources, else 0
    relevances = [1.0 if r.get("metadata", {}).get("source", "") in relevant_sources else 0.0
                  for r in retrieved[:k]]

    ideal_relevances = sorted(relevances, reverse=True)
    dcg_val = _dcg(relevances)
    idcg_val = _dcg(ideal_relevances)
    return dcg_val / idcg_val if idcg_val > 0 else 0.0


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def load_raw_results(path: Path | str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compute_metrics(raw_results: dict[str, Any]) -> EvaluationReport:
    """Compute all metrics from raw evaluation results."""
    records = raw_results["records"]
    config = raw_results.get("config", {})

    # Group by condition
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_condition[r["condition"]].append(r)

    condition_summaries: list[ConditionSummary] = []
    all_scenario_metrics: list[ScenarioMetrics] = []
    ndcg_results: list[NDCGResult] = []
    confusion_matrices: dict[str, dict[str, dict[str, int]]] = {}

    # Ground truth lookup for NDCG relevance
    scenario_by_id = {s["id"]: s for s in SCENARIOS}

    for condition, cond_records in by_condition.items():
        n = len(cond_records)
        if n == 0:
            continue

        # Per-scenario metrics
        cat_correct = 0
        actionability_scores = []
        times = []
        confidences = []
        per_cat: dict[str, dict[str, list]] = defaultdict(lambda: {"correct": [], "actionability": [], "count": 0})

        for r in cond_records:
            gt = r["ground_truth"]
            diag = r["diagnosis"]

            pred_cat = diag.get("failure_category", "unknown")
            expected_cat = gt["category"]
            correct = _category_match(pred_cat, expected_cat)

            actionability = _keyword_coverage(
                diag.get("recommended_fix", ""),
                gt.get("remediation_keywords", []),
            )

            elapsed = r.get("elapsed_seconds", 0.0)
            confidence = diag.get("confidence_score", 0.0)

            cat_correct += correct
            actionability_scores.append(actionability)
            times.append(elapsed)
            confidences.append(confidence)

            per_cat[expected_cat]["correct"].append(correct)
            per_cat[expected_cat]["actionability"].append(actionability)
            per_cat[expected_cat]["count"] += 1

            all_scenario_metrics.append(ScenarioMetrics(
                scenario_id=r["scenario_id"],
                condition=condition,
                category_correct=correct,
                actionability_score=actionability,
                elapsed_seconds=elapsed,
                predicted_category=pred_cat,
                expected_category=expected_cat,
                confidence_score=confidence,
            ))

            # Prefer actual ranked retrieval output persisted by the runner.
            # Fall back to model-reported references for backwards compatibility
            # with older raw-results files.
            actual_retrieval = r.get("retrieval_results") or []
            if actual_retrieval:
                retrieved_for_ndcg = actual_retrieval[:5]
                retrieved_refs = [
                    item.get("metadata", {}).get("source", "unknown")
                    for item in retrieved_for_ndcg
                ]
            else:
                retrieved_refs = diag.get("retrieved_references", [])
                retrieved_for_ndcg = [
                    {"metadata": {"source": ref}} for ref in retrieved_refs
                ]
            relevant = _relevant_sources_for_category(expected_cat)
            ndcg_val = _ndcg_at_k(retrieved_for_ndcg, relevant, k=5)

            ndcg_results.append(NDCGResult(
                scenario_id=r["scenario_id"],
                condition=condition,
                ndcg_at_k=ndcg_val,
                k=5,
                retrieved_sources=retrieved_refs,
                relevant_sources=list(relevant),
            ))

        # Per-category breakdown
        per_category_summary = {}
        for cat in _CATEGORIES:
            data = per_cat[cat]
            cnt = data["count"]
            if cnt > 0:
                per_category_summary[cat] = {
                    "accuracy": sum(data["correct"]) / cnt,
                    "actionability": float(np.mean(data["actionability"])) if data["actionability"] else 0.0,
                    "count": cnt,
                }
            else:
                per_category_summary[cat] = {"accuracy": 0.0, "actionability": 0.0, "count": 0}

        condition_summaries.append(ConditionSummary(
            condition=condition,
            condition_label=r.get("condition_label", condition),  # last record has it
            n_scenarios=n,
            accuracy=cat_correct / n,
            actionability_mean=float(np.mean(actionability_scores)) if actionability_scores else 0.0,
            time_to_diagnosis_mean=float(np.mean(times)) if times else 0.0,
            time_to_diagnosis_std=float(np.std(times)) if times else 0.0,
            confidence_mean=float(np.mean(confidences)) if confidences else 0.0,
            per_category=per_category_summary,
        ))

        confusion_matrices[condition] = _confusion_matrix(cond_records)

    return EvaluationReport(
        generated_at_utc=raw_results.get("generated_at_utc", ""),
        config=config,
        condition_summaries=condition_summaries,
        scenario_metrics=all_scenario_metrics,
        ndcg_results=ndcg_results,
        confusion_matrices=confusion_matrices,
    )


def _relevant_sources_for_category(category: str) -> set[str]:
    """Map failure category to the set of source names considered relevant."""
    mapping = {
        "dependency_error": {"dependency_docs", "github_actions_docs"},
        "docker_failure": {"docker_docs", "github_actions_docs"},
        "test_failure": {"test_docs", "github_actions_docs"},
        "iac_misconfiguration": {"iac_docs", "github_actions_docs"},
        "unknown": set(),
    }
    return mapping.get(category, set())


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def report_to_dict(report: EvaluationReport) -> dict[str, Any]:
    return {
        "generated_at_utc": report.generated_at_utc,
        "config": report.config,
        "condition_summaries": [asdict(cs) for cs in report.condition_summaries],
        "scenario_metrics": [asdict(sm) for sm in report.scenario_metrics],
        "ndcg_results": [asdict(nr) for nr in report.ndcg_results],
        "confusion_matrices": report.confusion_matrices,
    }


def save_report(report: EvaluationReport, out_path: Path | str) -> None:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report_to_dict(report), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def print_summary(report: EvaluationReport) -> None:
    print("\n" + "=" * 70)
    print("EVALUATION METRICS SUMMARY")
    print("=" * 70)
    print(f"Config: {report.config}")
    print()

    for cs in report.condition_summaries:
        print(f"--- {cs.condition_label} ({cs.condition}) ---")
        print(f"  Scenarios:       {cs.n_scenarios}")
        print(f"  Accuracy:        {cs.accuracy:.3f}")
        print(f"  Actionability:   {cs.actionability_mean:.3f}")
        print(f"  Time-to-diag:    {cs.time_to_diagnosis_mean:.3f}s ± {cs.time_to_diagnosis_std:.3f}s")
        print(f"  Confidence:      {cs.confidence_mean:.3f}")
        print("  Per-category:")
        for cat, vals in cs.per_category.items():
            if vals["count"] > 0:
                print(f"    {cat:25s}  acc={vals['accuracy']:.3f}  act={vals['actionability']:.3f}  n={vals['count']}")
        print()

    # NDCG summary
    if report.ndcg_results:
        print("NDCG@5 by condition:")
        by_cond: dict[str, list[float]] = defaultdict(list)
        for nr in report.ndcg_results:
            by_cond[nr.condition].append(nr.ndcg_at_k)
        for cond, vals in by_cond.items():
            print(f"  {cond}: mean={np.mean(vals):.3f}  std={np.std(vals):.3f}")
        print()

    # Confusion matrices
    for cond, cm in report.confusion_matrices.items():
        print(f"Confusion matrix — {cond}:")
        cats = [c for c in _CATEGORIES if any(cm.get(c, {}).values()) or any(row.get(c, 0) for row in cm.values())]
        header = "true\\pred".ljust(25) + "".join(c.ljust(10) for c in cats)
        print(f"  {header}")
        for true_cat in cats:
            row = true_cat.ljust(25)
            for pred_cat in cats:
                row += str(cm.get(true_cat, {}).get(pred_cat, 0)).ljust(10)
            print(f"  {row}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Compute evaluation metrics from raw_results.json")
    parser.add_argument("--input", required=True, help="Path to raw_results.json")
    parser.add_argument("--output", default="evaluation_results/metrics.json", help="Output path for metrics JSON")
    parser.add_argument("--print", action="store_true", help="Print summary to stdout")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    raw = load_raw_results(args.input)
    report = compute_metrics(raw)
    save_report(report, args.output)
    logger.info("Metrics written to %s", args.output)

    if args.print:
        print_summary(report)


if __name__ == "__main__":
    _cli()