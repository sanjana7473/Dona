"""
log_parser.py
Parses raw CI/CD failure log text into a structured summary that the
ReAct agent and retrieval layer can operate on.
"""

import re
from typing import TypedDict


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

class ParsedLog(TypedDict):
    raw_log: str
    error_lines: list[str]
    failure_category_hint: str   # coarse first-pass category
    summary: str                 # short human-readable summary


# ---------------------------------------------------------------------------
# Category heuristics
# ---------------------------------------------------------------------------

_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "dependency_error",
        re.compile(
            r"(ModuleNotFoundError|ImportError|could not find package|"
            r"version conflict|resolution failed|cannot install|"
            r"pip.*error|npm.*error|yarn.*error|cannot find module|"
            r"no matching distribution|ERESOLVE|peer dependency|"
            r"package.*not found)",
            re.IGNORECASE,
        ),
    ),
    (
        "docker_failure",
        re.compile(
            r"(docker.*error|error response from daemon|failed to pull|"
            r"pull access denied|manifest unknown|image.*not found|"
            r"layer.*already being pulled|build.*failed|"
            r"cannot connect to.*docker|dockerfile)",
            re.IGNORECASE,
        ),
    ),
    (
        "test_failure",
        re.compile(
            r"(FAILED|AssertionError|assertion.*failed|test.*error|"
            r"pytest.*failed|unittest.*failed|flaky|"
            r"expected.*but got|E\s+Assert)",
            re.IGNORECASE,
        ),
    ),
    (
        "iac_misconfiguration",
        re.compile(
            r"(terraform|invalid.*hcl|schema.*violation|kubernetes|"
            r"k8s|yaml.*invalid|helm.*error|manifest.*invalid|"
            r"provider.*error|resource.*already exists)",
            re.IGNORECASE,
        ),
    ),
]


def _detect_category(log: str) -> str:
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(log):
            return category
    return "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_log(raw_log: str) -> ParsedLog:
    """Return a structured representation of a raw CI/CD failure log."""
    lines = raw_log.splitlines()

    # Extract lines that look like errors / failures
    error_pattern = re.compile(
        r"(error|fail|exception|traceback|fatal|warn)",
        re.IGNORECASE,
    )
    error_lines = [line.strip() for line in lines if error_pattern.search(line)]

    failure_category_hint = _detect_category(raw_log)

    # Build a concise summary: category + first 3 error lines
    summary_parts = [f"[{failure_category_hint}]"]
    summary_parts.extend(error_lines[:3])
    summary = " | ".join(summary_parts)

    return ParsedLog(
        raw_log=raw_log,
        error_lines=error_lines,
        failure_category_hint=failure_category_hint,
        summary=summary,
    )
