"""Regression tests for parser-based agent fallbacks."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.agent.react_agent import DiagnosisAgent
from app.utils.log_parser import parse_log


_TERRAFORM_LOG = """Run terraform validate
╷
│ Error: Missing newline after argument
│
│   on main.tf line 8, in resource \"null_resource\" \"broken\":
│    8:     invalid_value =
│
│ An argument or block definition is required here.
╵
ERROR: Terraform configuration is invalid.
Error: Process completed with exit code 1.
"""


@pytest.fixture
def fallback_agent():
    store = MagicMock()
    with patch("app.agent.react_agent.ChatOpenAI") as llm_factory, \
         patch("app.agent.react_agent.create_react_agent") as create_agent:
        llm_factory.return_value = MagicMock()
        agent_runtime = MagicMock()
        create_agent.return_value = agent_runtime
        yield agent_runtime, DiagnosisAgent(store=store, mode="rag_full")


def test_agent_exception_uses_category_aware_fallback(fallback_agent):
    runtime, agent = fallback_agent
    runtime.invoke.side_effect = RuntimeError("OpenAI is unavailable")

    diagnosis = agent.diagnose(parse_log(_TERRAFORM_LOG))

    assert diagnosis["failure_category"] == "iac_misconfiguration"
    assert diagnosis["confidence_score"] > 0
    assert "Missing newline" in diagnosis["root_cause"]
    assert "terraform" in diagnosis["recommended_fix"].lower()
    assert diagnosis["diagnosis_mode"] == "rag_full"


def test_invalid_agent_json_uses_parser_fallback(fallback_agent):
    runtime, agent = fallback_agent
    runtime.invoke.return_value = {
        "messages": [SimpleNamespace(content="not valid JSON")]
    }

    diagnosis = agent.diagnose(parse_log(_TERRAFORM_LOG))

    assert diagnosis["failure_category"] == "iac_misconfiguration"
    assert diagnosis["root_cause"] != "Unable to determine root cause."
