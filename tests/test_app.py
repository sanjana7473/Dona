"""
test_app.py
Unit and integration tests for the CI/CD diagnosis application.

The agent and ChromaDB calls are mocked so the test suite runs without
requiring API keys or a live embedding model.
"""

import json
import pytest
from unittest.mock import MagicMock

from app import create_app
from config import Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    """Flask test client with a temp history path and mocked agent/store."""

    mock_store = MagicMock()
    mock_store.count.return_value = 5  # pretend KB already seeded
    mock_store.retrieve.return_value = [
        {
            "text": "Sample KB chunk about pip version conflicts.",
            "metadata": {"source": "dependency_docs", "category": "dependency_error"},
            "distance": 0.12,
        }
    ]

    mock_agent = MagicMock()
    mock_agent.diagnose.return_value = {
        "failure_category": "dependency_error",
        "root_cause": "Incompatible package versions.",
        "confidence_score": 0.85,
        "recommended_fix": "Pin the conflicting package version.",
        "retrieved_references": ["dependency_docs"],
    }

    class TestConfig(Config):
        TESTING = True
        DIAGNOSIS_HISTORY_PATH = str(tmp_path / "history.db")

    app = create_app(
        TestConfig,
        vector_store_factory=lambda **kwargs: mock_store,
        seed_func=lambda store: 0,
        agent_factory=lambda **kwargs: mock_agent,
    )

    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Health / main routes
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


# ---------------------------------------------------------------------------
# /api/diagnose
# ---------------------------------------------------------------------------

def test_diagnose_missing_log(client):
    res = client.post("/api/diagnose", json={})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_diagnose_empty_log(client):
    res = client.post("/api/diagnose", json={"log": "   "})
    assert res.status_code == 400


def test_diagnose_rejects_non_string_log(client):
    res = client.post("/api/diagnose", json={"log": 123})
    assert res.status_code == 400


def test_history_supports_pagination_and_category_filter(client):
    client.post("/api/diagnose", json={"log": "Error: package not found"})
    client.post("/api/diagnose", json={"log": "docker: Error response from daemon"})

    res = client.get("/api/history?page=1&page_size=1&category=dependency_error")
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["total"] == 2
    assert payload["page_size"] == 1
    assert len(payload["diagnoses"]) == 1
    assert len(payload["diagnoses"][0]["log_hash"]) == 64


def test_diagnose_returns_required_keys(client):
    res = client.post("/api/diagnose", json={"log": "Error: package not found"})
    assert res.status_code == 200
    data = res.get_json()
    for key in ("failure_category", "root_cause", "confidence_score",
                "recommended_fix", "retrieved_references"):
        assert key in data, f"Missing key: {key}"


def test_diagnose_returns_correct_category(client):
    res = client.post("/api/diagnose", json={"log": "ModuleNotFoundError: No module named requests"})
    assert res.status_code == 200
    assert res.get_json()["failure_category"] == "dependency_error"


# ---------------------------------------------------------------------------
# /api/history
# ---------------------------------------------------------------------------

def test_history_empty_initially(client):
    res = client.get("/api/history")
    assert res.status_code == 200
    assert res.get_json()["diagnoses"] == []


def test_history_persists_after_diagnosis(client):
    client.post("/api/diagnose", json={"log": "Error: package not found"})
    res = client.get("/api/history")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["diagnoses"]) == 1
    entry = data["diagnoses"][0]
    assert "log_summary" in entry
    assert "diagnosis" in entry
    assert "timestamp" in entry


def test_history_accumulates_multiple_diagnoses(client):
    client.post("/api/diagnose", json={"log": "Error: package not found"})
    client.post("/api/diagnose", json={"log": "docker: Error response from daemon"})
    res = client.get("/api/history")
    assert len(res.get_json()["diagnoses"]) == 2


# ---------------------------------------------------------------------------
# /api/knowledge-base/stats
# ---------------------------------------------------------------------------

def test_kb_stats_endpoint(client):
    res = client.get("/api/knowledge-base/stats")
    assert res.status_code == 200
    assert "chunk_count" in res.get_json()


# ---------------------------------------------------------------------------
# Log parser unit tests
# ---------------------------------------------------------------------------

from app.utils.log_parser import parse_log


def test_parse_log_detects_dependency_error():
    log = "ERROR: Cannot install requests because urllib3 requires charset-normalizer>=2.0 but you have charset-normalizer==1.4"
    result = parse_log(log)
    assert result["failure_category_hint"] == "dependency_error"
    assert len(result["error_lines"]) >= 1


def test_parse_log_detects_docker_failure():
    log = "Error response from daemon: pull access denied for myimage, repository does not exist"
    result = parse_log(log)
    assert result["failure_category_hint"] == "docker_failure"


def test_parse_log_detects_test_failure():
    log = "FAILED tests/test_api.py::test_create_user - AssertionError: assert 404 == 200"
    result = parse_log(log)
    assert result["failure_category_hint"] == "test_failure"


def test_parse_log_detects_iac_misconfiguration():
    log = "Error: Invalid HCL in main.tf: An argument or block definition is required here."
    result = parse_log(log)
    assert result["failure_category_hint"] == "iac_misconfiguration"


def test_parse_log_unknown_category():
    result = parse_log("Something completely unrecognisable happened.")
    assert result["failure_category_hint"] == "unknown"


def test_parse_log_summary_contains_category():
    result = parse_log("ModuleNotFoundError: No module named flask")
    assert "dependency_error" in result["summary"]

