"""
test_evaluation.py
------------------
Tests for the Update 4 evaluation harness: scenarios dataset integrity,
ablation conditions, and the runner (with the LLM and store mocked).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evaluation import ABLATION_CONDITIONS, build_agent_for_condition
from evaluation.conditions import CONDITION_LABELS, CONDITION_SPECS
from evaluation.scenarios import (
    SCENARIOS,
    get_scenario,
    list_scenarios,
    scenario_ids,
    scenarios_by_category,
)
from evaluation.github_logs import load_github_artifacts
from evaluation.runner import run_evaluation


# ---------------------------------------------------------------------------
# Scenario corpus integrity
# ---------------------------------------------------------------------------

def test_scenario_corpus_meets_minimum_size():
    assert len(SCENARIOS) >= 15, "Paper requires 15–20 injected scenarios."
    assert len(SCENARIOS) <= 20


def test_all_scenarios_have_unique_ids():
    ids = [s["id"] for s in SCENARIOS]
    assert len(ids) == len(set(ids)), "Duplicate scenario IDs detected."


def test_all_four_categories_present():
    categories = set(s["category"] for s in SCENARIOS)
    assert categories == {
        "dependency_error",
        "docker_failure",
        "test_failure",
        "iac_misconfiguration",
    }


def test_each_category_has_at_least_four_scenarios():
    by_cat = scenarios_by_category()
    for cat, items in by_cat.items():
        assert len(items) >= 4, f"Category {cat} has only {len(items)} scenarios."


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["id"])
def test_every_scenario_has_required_fields(scenario):
    for key in (
        "id",
        "category",
        "description",
        "raw_log",
        "expected_root_cause",
        "remediation_keywords",
        "source",
    ):
        assert key in scenario, f"{scenario['id']} missing field {key}"
    assert scenario["raw_log"].strip(), f"{scenario['id']} has empty raw_log"
    assert scenario["remediation_keywords"], f"{scenario['id']} has no remediation keywords"
    assert scenario["source"] == "synthetic"


def test_scenario_accessors():
    assert len(list_scenarios()) == len(SCENARIOS)
    first_id = SCENARIOS[0]["id"]
    assert get_scenario(first_id) == SCENARIOS[0]
    assert get_scenario("DOES-NOT-EXIST") is None
    assert len(scenario_ids()) == len(SCENARIOS)


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def test_three_ablation_conditions_defined():
    assert ABLATION_CONDITIONS == ("llm_only", "rag_docs", "rag_full")


def test_condition_specs_cover_all_conditions():
    assert {s["condition"] for s in CONDITION_SPECS} == set(ABLATION_CONDITIONS)
    for spec in CONDITION_SPECS:
        assert spec["label"] == CONDITION_LABELS[spec["condition"]]


def test_build_agent_llm_only_requires_no_store():
    with patch("app.agent.react_agent.ChatOpenAI"), \
         patch("app.agent.react_agent.create_react_agent") as mock_create:
        build_agent_for_condition("llm_only", store=None)
        args, kwargs = mock_create.call_args
        # No tools should be passed in the LLM-only condition.
        assert kwargs.get("tools") == [] or (len(args) >= 2 and args[1] == [])


def test_build_agent_rag_docs_requires_store():
    with pytest.raises(ValueError):
        build_agent_for_condition("rag_docs", store=None)


def test_build_agent_rag_full_requires_store():
    with pytest.raises(ValueError):
        build_agent_for_condition("rag_full", store=None)


def test_build_agent_rejects_unknown_condition():
    with pytest.raises(ValueError):
        build_agent_for_condition("nope", store=MagicMock())


# ---------------------------------------------------------------------------
# Runner (mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def mocked_store():
    store = MagicMock()
    store.count.return_value = 10
    store.retrieve.return_value = [
        {
            "text": "mock chunk",
            "metadata": {"source": "dependency_docs", "category": "dependency_error"},
            "distance": 0.1,
        }
    ]
    return store


def _patched_agent_diagnosis(scenario):
    return {
        "failure_category": scenario["category"],
        "root_cause": scenario["expected_root_cause"],
        "confidence_score": 0.9,
        "recommended_fix": " ".join(scenario["remediation_keywords"]),
        "retrieved_references": ["dependency_docs"],
    }


def test_runner_writes_raw_results(tmp_path, mocked_store):
    scenarios = SCENARIOS[:3]

    with patch("app.agent.react_agent.ChatOpenAI"), \
         patch("app.agent.react_agent.create_react_agent") as mock_create, \
         patch(
             "evaluation.runner.build_agent_for_condition",
             autospec=True,
         ) as mock_build:

        def _make_agent(condition, store=None):
            agent = MagicMock()
            agent.diagnose.side_effect = lambda parsed: _patched_agent_diagnosis(
                next(s for s in scenarios if s["raw_log"] == parsed["raw_log"])
            )
            return agent

        mock_build.side_effect = _make_agent
        # prevent the real DiagnosisAgent from being instantiated
        mock_create.return_value = MagicMock()

        out_path = run_evaluation(
            store=mocked_store,
            conditions=("llm_only", "rag_docs", "rag_full"),
            scenarios=scenarios,
            out_dir=tmp_path,
        )

    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["num_scenarios"] == 3
    assert payload["num_conditions"] == 3
    assert len(payload["records"]) == 9  # 3 scenarios x 3 conditions
    # Each record has the expected schema.
    for rec in payload["records"]:
        assert set(rec.keys()) >= {
            "scenario_id", "condition", "condition_label",
            "ground_truth", "diagnosis", "elapsed_seconds", "timestamp_utc",
        }
        assert set(rec["ground_truth"].keys()) == {
            "category", "expected_root_cause", "remediation_keywords",
        }


def test_runner_requires_store_when_rag_condition_requested(tmp_path):
    with pytest.raises(ValueError):
        run_evaluation(
            store=None,
            conditions=("rag_docs",),
            scenarios=SCENARIOS[:1],
            out_dir=tmp_path,
        )


def test_runner_rejects_unknown_condition(tmp_path, mocked_store):
    with pytest.raises(ValueError):
        run_evaluation(
            store=mocked_store,
            conditions=("bogus",),  # type: ignore[arg-type]
            scenarios=SCENARIOS[:1],
            out_dir=tmp_path,
        )


def test_load_github_artifacts(tmp_path):
    artifact_dir = tmp_path / "failure-log-DEP-GHA-001"
    artifact_dir.mkdir()
    (artifact_dir / "failure-DEP-GHA-001.log").write_text("ERROR: dependency conflict", encoding="utf-8")
    (artifact_dir / "ground_truth.json").write_text(json.dumps({
        "scenario_id": "DEP-GHA-001",
        "category": "dependency_error",
        "description": "pip dependency conflict",
        "expected_root_cause": "incompatible package versions",
        "remediation_keywords": ["requirements.txt", "pin"],
        "source": "github_actions_synthetic",
    }), encoding="utf-8")

    scenarios = load_github_artifacts(tmp_path)
    assert len(scenarios) == 1
    assert scenarios[0]["id"] == "DEP-GHA-001"
    assert scenarios[0]["source"] == "github_actions_synthetic"
    assert scenarios[0]["raw_log"] == "ERROR: dependency conflict"


def test_runner_llm_only_can_run_without_store(tmp_path):
    scenarios = SCENARIOS[:2]
    with patch("evaluation.runner.build_agent_for_condition", autospec=True) as mock_build:
        agent = MagicMock()
        agent.diagnose.side_effect = lambda parsed: {
            "failure_category": "unknown",
            "root_cause": "mocked",
            "confidence_score": 0.0,
            "recommended_fix": "",
            "retrieved_references": [],
        }
        mock_build.return_value = agent

        out_path = run_evaluation(
            store=None,
            conditions=("llm_only",),
            scenarios=scenarios,
            out_dir=tmp_path,
        )

    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["num_conditions"] == 1
    assert all(r["condition"] == "llm_only" for r in payload["records"])
