"""Shared diagnosis pipeline used by uploaded and generated logs."""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask
from pydantic import ValidationError

from app.utils.log_parser import parse_log
from app.utils.validation import validate_diagnosis

logger = logging.getLogger(__name__)


class InvalidLogError(ValueError):
    """The submitted log is missing, empty, or too large."""


class AgentDiagnosisError(RuntimeError):
    """The diagnosis agent failed or returned an invalid response."""


class HistoryPersistenceError(RuntimeError):
    """The diagnosis could not be written to history."""


def diagnose_and_persist(
    app: Flask,
    raw_log: str,
    *,
    source: str = "manual_upload",
) -> dict[str, Any]:
    """Run the complete diagnosis pipeline for one raw log.

    The source is stored in history for UI filtering and provenance. It is
    metadata only and is never included in the agent prompt.
    """
    if not isinstance(raw_log, str):
        raise InvalidLogError("'log' field must be a string")
    if not raw_log.strip():
        raise InvalidLogError("'log' field must not be empty")
    if len(raw_log) > app.config["MAX_LOG_CHARS"]:
        raise InvalidLogError(
            f"'log' field exceeds the {app.config['MAX_LOG_CHARS']} character limit"
        )

    parsed = parse_log(raw_log)
    agent = app.extensions["diagnosis_agent"]
    try:
        diagnosis = validate_diagnosis(agent.diagnose(parsed))
    except (ValidationError, TypeError, ValueError) as exc:
        raise AgentDiagnosisError("Diagnosis agent returned an invalid response") from exc
    except Exception as exc:
        raise AgentDiagnosisError("Diagnosis agent failed") from exc

    # A diagnosis should remain available even if feedback indexing fails.
    try:
        app.extensions["vector_store"].add_failure_fix_pair(parsed["summary"], diagnosis)
    except Exception:
        logger.exception("Could not persist diagnosis feedback to the knowledge base")

    try:
        app.extensions["history_store"].add(
            raw_log=raw_log,
            log_summary=parsed["summary"],
            failure_category_hint=parsed["failure_category_hint"],
            diagnosis=diagnosis,
            source=source,
        )
    except Exception as exc:
        raise HistoryPersistenceError("Could not persist diagnosis history") from exc

    return diagnosis
