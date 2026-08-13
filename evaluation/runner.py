"""
runner.py
---------
Evaluation harness that executes every injected failure scenario under each
ablation condition and persists the raw per-scenario results to disk.

This is the scaffolding for the experimental harness described in the paper
(Section IV.E).  Metric computation (accuracy, actionability, NDCG, etc.)
is implemented in Update 5; this module is responsible only for *running*
the experiments and saving raw outputs in a stable, replayable format.

Usage
-----
    from evaluation.runner import run_evaluation
    run_evaluation(store=my_store)  # writes ./evaluation_results/raw_results.json

Or from the command line:

    python -m evaluation.runner [--out PATH] [--conditions llm_only,rag_full]

Reproducibility notes
---------------------
* The LLM is invoked with ``temperature=0`` (see react_agent.py).
* Each (scenario, condition) pair is recorded with start/end timestamps so
  time-to-diagnosis can be computed later without re-running.
* A fresh agent is constructed for each condition. The runner does not write
  new feedback pairs during evaluation, so scenarios are evaluated against a
  stable knowledge-base snapshot and cannot contaminate later scenarios.
* Results are written incrementally so a partially-completed run is still
  inspectable.
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.rag.store import VectorStore
from app.utils.log_parser import parse_log
from config import Config

from evaluation.conditions import (
    ABLATION_CONDITIONS,
    DiagnosisCondition,
    CONDITION_LABELS,
    build_agent_for_condition,
)
from evaluation.github_logs import load_github_artifacts
from evaluation.scenarios import FailureScenario, SCENARIOS

logger = logging.getLogger(__name__)

DEFAULT_RESULTS_DIR = Path("./evaluation_results")
DEFAULT_RESULTS_FILE = "raw_results.json"


# ---------------------------------------------------------------------------
# Result record schema
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_record(
    scenario: FailureScenario,
    condition: DiagnosisCondition,
    diagnosis: dict[str, Any],
    elapsed_seconds: float,
    retrieval_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble one result record for a single (scenario, condition) run."""
    return {
        "scenario_id": scenario["id"],
        "condition": condition,
        "condition_label": CONDITION_LABELS[condition],
        "ground_truth": {
            "category": scenario["category"],
            "expected_root_cause": scenario["expected_root_cause"],
            "remediation_keywords": scenario["remediation_keywords"],
        },
        "diagnosis": diagnosis,
        "retrieval_results": retrieval_results or [],
        "elapsed_seconds": round(elapsed_seconds, 4),
        "timestamp_utc": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Run helpers
# ---------------------------------------------------------------------------

def _run_one(
    scenario: FailureScenario,
    condition: DiagnosisCondition,
    store: VectorStore | None,
    agent: Any | None = None,
) -> dict[str, Any]:
    """Run a single scenario under one condition and return a result record."""
    agent = agent or build_agent_for_condition(condition, store=store)
    parsed = parse_log(scenario["raw_log"])

    start = time.perf_counter()
    diagnosis = agent.diagnose(parsed)
    elapsed = time.perf_counter() - start

    retrieval_results = getattr(agent, "retrieval_results", [])
    if not isinstance(retrieval_results, list):
        retrieval_results = []
    return _build_record(scenario, condition, diagnosis, elapsed, retrieval_results)


def _run_condition(
    condition: DiagnosisCondition,
    scenarios: Iterable[FailureScenario],
    store: VectorStore | None,
) -> list[dict[str, Any]]:
    """Run all scenarios under a single condition."""
    logger.info("=== Condition: %s ===", CONDITION_LABELS[condition])
    records: list[dict[str, Any]] = []
    agent = build_agent_for_condition(condition, store=store)
    for scenario in scenarios:
        logger.info("  [%s] %s", scenario["id"], scenario["description"])
        record = _run_one(scenario, condition, store, agent=agent)
        records.append(record)
        logger.info(
            "    -> category=%s confidence=%.2f elapsed=%.2fs",
            record["diagnosis"].get("failure_category"),
            record["diagnosis"].get("confidence_score", 0.0),
            record["elapsed_seconds"],
        )
    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_evaluation(
    store: VectorStore | None = None,
    conditions: Iterable[DiagnosisCondition] = ABLATION_CONDITIONS,
    scenarios: Iterable[FailureScenario] | None = None,
    out_dir: Path | str = DEFAULT_RESULTS_DIR,
    out_file: str = DEFAULT_RESULTS_FILE,
) -> Path:
    """Run the full evaluation matrix and persist raw results.

    Returns the path to the written results file.

    Parameters
    ----------
    store
        VectorStore instance used by the RAG conditions.  May be ``None`` if
        only the ``llm_only`` condition is requested.
    conditions
        Subset of conditions to run; defaults to all three.
    scenarios
        Scenarios to evaluate; defaults to the full corpus.
    out_dir / out_file
        Where to persist the JSON results.
    """
    conditions = list(conditions)
    scenarios = list(scenarios) if scenarios is not None else SCENARIOS

    # Validate conditions.
    for c in conditions:
        if c not in ABLATION_CONDITIONS:
            raise ValueError(f"Unknown condition {c!r}; expected one of {ABLATION_CONDITIONS}.")

    needs_store = any(c in ("rag_docs", "rag_full") for c in conditions)
    if needs_store and store is None:
        raise ValueError("A VectorStore is required when running RAG conditions.")

    out_path = Path(out_dir) / out_file
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_records: list[dict[str, Any]] = []
    for condition in conditions:
        records = _run_condition(condition, scenarios, store if condition != "llm_only" else None)
        all_records.extend(records)
        # Incremental flush so partial runs are inspectable.
        _flush(all_records, out_path)

    _flush(all_records, out_path)
    logger.info("Wrote %d raw result records to %s", len(all_records), out_path)
    return out_path


def _flush(records: list[dict[str, Any]], out_path: Path) -> None:
    payload = {
        "generated_at_utc": _now_iso(),
        "config": {
            "llm_model": Config.LLM_MODEL,
            "embedding_model": Config.EMBEDDING_MODEL,
            "top_k": Config.TOP_K_RESULTS,
            "chunk_size": Config.CHUNK_SIZE,
            "chunk_overlap": Config.CHUNK_OVERLAP,
        },
        "num_scenarios": len({r["scenario_id"] for r in records}),
        "num_conditions": len({r["condition"] for r in records}),
        "records": records,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the CI/CD failure-diagnosis evaluation matrix.",
    )
    p.add_argument(
        "--conditions",
        default=",".join(ABLATION_CONDITIONS),
        help="Comma-separated subset of conditions to run "
             f"(one of {','.join(ABLATION_CONDITIONS)}).",
    )
    p.add_argument(
        "--out",
        default=str(DEFAULT_RESULTS_DIR),
        help="Directory to write raw_results.json into.",
    )
    p.add_argument(
        "--persist-dir",
        default=Config.CHROMA_PERSIST_DIR,
        help="ChromaDB persist directory (used by RAG conditions).",
    )
    p.add_argument(
        "--github-artifacts",
        help="Directory containing downloaded GitHub Actions failure-log artifacts.",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = _parse_args()

    conditions: list[DiagnosisCondition] = [
        c.strip() for c in args.conditions.split(",") if c.strip()
    ]

    store: VectorStore | None = None
    if any(c in ("rag_docs", "rag_full") for c in conditions):
        store = VectorStore(persist_dir=args.persist_dir)
        if store.count() == 0:
            logger.warning(
                "ChromaDB at %s is empty. Seeding the knowledge base first…",
                args.persist_dir,
            )
            from app.rag.seeder import seed_knowledge_base
            n = seed_knowledge_base(store)
            logger.info("Seeded %d chunks.", n)

    scenarios = (
        load_github_artifacts(args.github_artifacts)
        if args.github_artifacts
        else SCENARIOS
    )
    run_evaluation(
        store=store,
        conditions=conditions,
        scenarios=scenarios,
        out_dir=Path(args.out),
    )


if __name__ == "__main__":
    main()
