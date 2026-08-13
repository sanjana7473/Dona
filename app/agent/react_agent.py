"""
react_agent.py
LangChain / LangGraph ReAct agent for CI/CD failure diagnosis.

The agent follows the Observe → Reason → Act loop described in the paper:
  1. Receives a structured log summary.
  2. Calls the ``retrieve_context`` tool to fetch relevant KB chunks.
  3. Reasons over the retrieved context.
  4. Produces a structured JSON diagnosis.
"""

import json
import logging
from typing import Any, Literal, TypedDict

from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from app.rag.store import VectorStore
from app.utils.log_parser import ParsedLog
from app.utils.validation import validate_diagnosis
from config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ablation modes (per the paper's experimental design, Section IV.E)
# ---------------------------------------------------------------------------

DiagnosisMode = Literal["llm_only", "rag_docs", "rag_full"]


class DiagnosisState(TypedDict, total=False):
    """State carried through parsing, retrieval, reasoning, and validation."""

    parsed_log: ParsedLog
    retrieval_results: list[dict[str, Any]]
    diagnosis: dict[str, Any]


_VALID_MODES: tuple[DiagnosisMode, ...] = ("llm_only", "rag_docs", "rag_full")

_SYSTEM_PROMPT_WITH_RAG = """You are an expert CI/CD failure diagnosis assistant.
Your task is to analyse a GitHub Actions pipeline failure log, retrieve
relevant context from the knowledge base, identify the root cause, and
produce a structured diagnosis.

Always call the retrieve_context tool at least once before forming your
final answer so your diagnosis is grounded in retrieved evidence.

Your final response MUST be valid JSON with exactly these keys:
{
  "failure_category": "<dependency_error|docker_failure|test_failure|iac_misconfiguration|unknown>",
  "root_cause": "<concise explanation of the root cause>",
  "confidence_score": <float between 0.0 and 1.0>,
  "recommended_fix": "<step-by-step remediation instructions>",
  "retrieved_references": [<list of short strings summarising retrieved sources>]
}

Do not include any text outside the JSON object."""

_SYSTEM_PROMPT_NO_RAG = """You are an expert CI/CD failure diagnosis assistant.
Your task is to analyse a GitHub Actions pipeline failure log, identify the
root cause, and produce a structured diagnosis using only your parametric
knowledge.  Do NOT attempt to call any tools — none are available in this
condition.

Your final response MUST be valid JSON with exactly these keys:
{
  "failure_category": "<dependency_error|docker_failure|test_failure|iac_misconfiguration|unknown>",
  "root_cause": "<concise explanation of the root cause>",
  "confidence_score": <float between 0.0 and 1.0>,
  "recommended_fix": "<step-by-step remediation instructions>",
  "retrieved_references": []
}

Do not include any text outside the JSON object."""

# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

_FALLBACK_DIAGNOSIS: dict[str, Any] = {
    "failure_category": "unknown",
    "root_cause": "Unable to determine root cause from the available log evidence.",
    "confidence_score": 0.0,
    "recommended_fix": "Review the full log manually.",
    "retrieved_references": [],
}

_FALLBACK_GUIDANCE: dict[str, tuple[str, str]] = {
    "dependency_error": (
        "The dependency installation failed because package requirements are missing or incompatible.",
        "Review requirements.txt or package.json, choose compatible versions, reinstall dependencies, and rerun the CI job.",
    ),
    "docker_failure": (
        "The Docker build or image retrieval failed because the image, tag, Dockerfile, or Docker daemon configuration is invalid.",
        "Check the Dockerfile instructions and base-image tag, verify registry access, then rerun docker build.",
    ),
    "test_failure": (
        "The test command failed because a test assertion, test setup, or required test environment value did not match the expected condition.",
        "Inspect the failing test and assertion values, correct the implementation or test fixture, and rerun the test suite.",
    ),
    "iac_misconfiguration": (
        "Infrastructure-as-Code validation failed because the Terraform, Kubernetes, or other infrastructure configuration is invalid.",
        "Correct the configuration syntax or schema placement, run terraform fmt and terraform validate locally, then rerun the CI validation step.",
    ),
}


def _heuristic_fallback(parsed_log: ParsedLog | None) -> dict[str, Any]:
    """Create a useful parser-based diagnosis when the LLM cannot respond."""
    if not parsed_log:
        return dict(_FALLBACK_DIAGNOSIS)

    category = parsed_log["failure_category_hint"]
    guidance = _FALLBACK_GUIDANCE.get(category)
    if not guidance:
        return dict(_FALLBACK_DIAGNOSIS)

    evidence = next(
        (
            line
            for line in parsed_log["error_lines"]
            if line and "process completed with exit code" not in line.lower()
        ),
        "The log contains a failure signal but no more specific error line was extracted.",
    )
    root_cause = f"{guidance[0]} Evidence from the log: {evidence}"
    return validate_diagnosis(
        {
            "failure_category": category,
            "root_cause": root_cause,
            "confidence_score": 0.76,
            "recommended_fix": guidance[1],
            "retrieved_references": [],
        }
    )


def _parse_agent_output(
    raw: str,
    parsed_log: ParsedLog | None = None,
) -> dict[str, Any]:
    """Extract JSON and validate it, using parser evidence on invalid output."""
    fallback = _heuristic_fallback(parsed_log)
    if not isinstance(raw, str):
        return fallback

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    candidates = [cleaned]
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        candidates.append(raw[start:end])

    for candidate in candidates:
        try:
            return validate_diagnosis(json.loads(candidate))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    logger.warning("Could not validate agent JSON output; using parser-based fallback.")
    return fallback


# ---------------------------------------------------------------------------
# ReAct agent factory
# ---------------------------------------------------------------------------

class DiagnosisAgent:
    """Wraps the LangGraph ReAct agent with a ChromaDB retrieval tool.

    Parameters
    ----------
    store
        The ChromaDB-backed VectorStore used for retrieval. May be ``None``
        when ``mode == "llm_only"`` (no retrieval needed).
    mode
        Ablation condition controlling retrieval behaviour:
          * ``"llm_only"``  – no retrieval tool exposed (LLM-only baseline).
          * ``"rag_docs"``  – retrieve only curated documentation chunks
            (exclude ``feedback_loop`` entries).
          * ``"rag_full"``  – retrieve from the full knowledge base
            (documentation + historical failure-fix pairs).
    """

    def __init__(
        self,
        store: VectorStore | None,
        mode: DiagnosisMode = "rag_full",
        *,
        llm_model: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        top_k: int | None = None,
    ):
        if mode not in _VALID_MODES:
            raise ValueError(f"Invalid mode {mode!r}; expected one of {_VALID_MODES}.")
        if mode != "llm_only" and store is None:
            raise ValueError("A VectorStore is required for any RAG mode.")

        self._store = store
        self._mode: DiagnosisMode = mode
        self._top_k = top_k or Config.TOP_K_RESULTS
        self._last_retrieval_results: list[dict[str, Any]] = []
        self._llm = ChatOpenAI(
            model=llm_model or Config.LLM_MODEL,
            api_key=api_key if api_key is not None else Config.OPENAI_API_KEY,
            temperature=temperature if temperature is not None else Config.LLM_TEMPERATURE,
        )
        self._agent = self._build_agent()

    @property
    def mode(self) -> DiagnosisMode:
        return self._mode

    @property
    def retrieval_results(self) -> list[dict[str, Any]]:
        """Return the actual ranked retrieval results from the last diagnosis."""
        return list(self._last_retrieval_results)

    def _build_agent(self):
        # LLM-only baseline: no tools, no retrieval.
        if self._mode == "llm_only":
            return create_react_agent(model=self._llm, tools=[])

        store = self._store  # capture for closure
        mode = self._mode

        @tool
        def retrieve_context(query: str) -> str:
            """Retrieve the most relevant CI/CD knowledge-base chunks for *query*.

            Use this tool to ground your diagnosis in factual documentation and
            historical failure-fix pairs before forming a conclusion.
            """
            results = store.retrieve(
                query,
                k=self._top_k,
                exclude_categories=["feedback_loop"] if mode == "rag_docs" else None,
            )
            self._last_retrieval_results = list(results)
            if not results:
                return "No relevant documents found in the knowledge base."
            parts = []
            for i, r in enumerate(results, 1):
                src = r.get("metadata", {}).get("source", "unknown")
                cat = r.get("metadata", {}).get("category", "")
                parts.append(
                    f"[{i}] (source={src}, category={cat}, distance={r.get('distance', 0.0)})\n"
                    f"{r.get('text', '')[:500]}"
                )
            return "\n\n".join(parts)

        return create_react_agent(model=self._llm, tools=[retrieve_context])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def diagnose(self, parsed_log: ParsedLog) -> dict[str, Any]:
        """Run the ReAct agent on *parsed_log* and return a validated diagnosis."""
        self._last_retrieval_results = []
        state: DiagnosisState = {"parsed_log": parsed_log}
        user_message = (
            f"Category hint: {parsed_log['failure_category_hint']}\n\n"
            f"Log summary: {parsed_log['summary']}\n\n"
            f"Error lines:\n" + "\n".join(parsed_log["error_lines"][:20]) +
            f"\n\nFull log (first {Config.RETRIEVAL_MAX_LOG_CHARS} chars):\n"
            f"{parsed_log['raw_log'][:Config.RETRIEVAL_MAX_LOG_CHARS]}"
        )

        system_prompt = (
            _SYSTEM_PROMPT_NO_RAG if self._mode == "llm_only" else _SYSTEM_PROMPT_WITH_RAG
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]

        result: dict[str, Any] = {}
        try:
            result = self._agent.invoke({"messages": messages})
            # LangGraph returns a dict with a 'messages' list; last is AI reply
            final_message = result["messages"][-1].content
            diagnosis = _parse_agent_output(final_message, parsed_log)
        except Exception as exc:
            logger.exception("Agent invocation failed: %s", exc)
            diagnosis = _heuristic_fallback(parsed_log)

        if self._last_retrieval_results and not diagnosis.get("retrieved_references"):
            diagnosis["retrieved_references"] = list(dict.fromkeys(
                result.get("metadata", {}).get("source", "unknown")
                for result in self._last_retrieval_results
            ))
        diagnosis["diagnosis_mode"] = self._mode
        state["retrieval_results"] = self.retrieval_results
        state["diagnosis"] = diagnosis
        return diagnosis
