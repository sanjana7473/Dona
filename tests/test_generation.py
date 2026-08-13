"""Tests for local and GitHub-generated failure-log flows."""

from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import MagicMock

import pytest

from app import _configure_quiet_dependency_logging, create_app
from app.generation.local import generate_local_failure
from app.agent.verifier import DiagnosisVerifier
from app.integrations.github import GitHubGenerationService
from app.utils.log_parser import parse_log
from config import Config


class FakeVerifier:
    def verify(self, raw_log, diagnosis):
        return {
            "is_correct": True,
            "confidence_score": 0.95,
            "explanation": "The diagnosis matches the failure evidence.",
            "evidence": ["dependency conflict"],
            "verification_mode": "ai_reviewer",
        }


class FakeGitHubService:
    def start(self, *, category, ref=None):
        return {
            "job_id": "gha-test-1",
            "category": category,
            "ref": ref or "main",
            "status": "dispatched",
        }

    def get(self, job_id):
        if job_id != "gha-test-1":
            return None
        return {"job_id": job_id, "status": "completed", "diagnosis": {}}


@pytest.fixture
def generation_client(tmp_path):
    store = MagicMock()
    store.count.return_value = 1
    agent = MagicMock()
    agent.diagnose.return_value = {
        "failure_category": "dependency_error",
        "root_cause": "dependency conflict",
        "confidence_score": 0.8,
        "recommended_fix": "update requirements.txt",
        "retrieved_references": [],
    }

    class TestConfig(Config):
        TESTING = True
        DIAGNOSIS_HISTORY_PATH = str(tmp_path / "history.db")
        OPENAI_API_KEY = None

    app = create_app(
        TestConfig,
        vector_store_factory=lambda **kwargs: store,
        seed_func=lambda store: 0,
        agent_factory=lambda **kwargs: agent,
        github_service_factory=lambda app: FakeGitHubService(),
        verifier_factory=lambda **kwargs: FakeVerifier(),
    )
    with app.test_client() as client:
        yield client


def test_quiet_dependency_logging_is_targeted():
    _configure_quiet_dependency_logging()
    import logging

    assert logging.getLogger("chromadb.telemetry").disabled is True
    assert logging.getLogger("posthog").disabled is True


def test_local_generation_is_reproducible_with_seed():
    first = generate_local_failure("dependency_error", seed=42)
    second = generate_local_failure("dependency_error", seed=42)

    assert first.raw_log == second.raw_log
    assert first.log_id == second.log_id
    assert first.category == "dependency_error"
    assert first.remediation_keywords


def test_random_local_generation_covers_a_supported_category():
    generated = generate_local_failure("random", seed=3)
    assert generated.category in {
        "dependency_error",
        "docker_failure",
        "test_failure",
        "iac_misconfiguration",
    }
    assert parse_log(generated.raw_log)["failure_category_hint"] == generated.category


def test_local_generation_rejects_unknown_category():
    with pytest.raises(ValueError):
        generate_local_failure("not-a-category")


def test_local_generation_endpoint_returns_log_and_diagnosis(generation_client):
    response = generation_client.post(
        "/api/generate/local",
        json={"category": "dependency_error", "seed": 42},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "local_generated"
    assert payload["log"]
    assert payload["ground_truth"]["category"] == "dependency_error"
    assert payload["diagnosis"]["failure_category"] == "dependency_error"

    history = generation_client.get("/api/history").get_json()
    assert history["diagnoses"][0]["source"] == "local_generated"


def test_local_generation_endpoint_validates_category(generation_client):
    response = generation_client.post(
        "/api/generate/local", json={"category": "invalid"}
    )
    assert response.status_code == 400
    assert "supported_categories" in response.get_json()


def test_verify_endpoint_returns_ai_review(generation_client):
    response = generation_client.post(
        "/api/verify",
        json={
            "log": "ERROR: dependency conflict",
            "diagnosis": {
                "failure_category": "dependency_error",
                "root_cause": "dependency conflict",
                "confidence_score": 0.8,
                "recommended_fix": "update requirements.txt",
                "retrieved_references": [],
            },
        },
    )
    assert response.status_code == 200
    assert response.get_json()["is_correct"] is True
    assert response.get_json()["verification_mode"] == "ai_reviewer"


def test_verify_endpoint_validates_required_fields(generation_client):
    response = generation_client.post("/api/verify", json={"log": "failure"})
    assert response.status_code == 400


def test_verifier_parses_structured_ai_response():
    result = DiagnosisVerifier._parse_response(
        """```json
        {"is_correct": false, "confidence_score": 0.9,
         "explanation": "The category is wrong.",
         "evidence": ["ModuleNotFoundError"],
         "corrected_failure_category": "dependency_error",
         "corrected_root_cause": "A package is missing.",
         "corrected_recommended_fix": "Install the package."}
        ```"""
    )
    assert result["is_correct"] is False
    assert result["corrected_failure_category"] == "dependency_error"


def test_github_generation_routes_use_service(generation_client):
    response = generation_client.post(
        "/api/github/generate",
        json={"category": "docker_failure", "ref": "main"},
    )
    assert response.status_code == 202
    assert response.get_json()["job_id"] == "gha-test-1"

    status = generation_client.get("/api/github/generate/gha-test-1")
    assert status.status_code == 200
    assert status.get_json()["status"] == "completed"


def test_github_generation_rejects_invalid_category(generation_client):
    response = generation_client.post(
        "/api/github/generate", json={"category": "invalid"}
    )
    assert response.status_code == 400


def test_github_artifact_extraction_reads_log_and_metadata():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("failure-DEP-GHA-001.log", "ERROR: dependency conflict")
        archive.writestr(
            "ground_truth.json",
            json.dumps({"category": "dependency_error"}),
        )

    raw_log, ground_truth = GitHubGenerationService._extract_artifact(buffer.getvalue())
    assert raw_log == "ERROR: dependency conflict"
    assert ground_truth == {"category": "dependency_error"}


def test_github_artifact_selection_matches_category():
    artifact = GitHubGenerationService._select_artifact(
        [
            {"id": 1, "name": "failure-log-DOC-GHA-001"},
            {"id": 2, "name": "failure-log-DEP-GHA-001"},
        ],
        "dependency_error",
    )
    assert artifact["id"] == 2
