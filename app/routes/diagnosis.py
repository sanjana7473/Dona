"""Diagnosis and history API routes."""

import logging

from flask import Blueprint, current_app, jsonify, request

from app.agent.verifier import VerificationError
from app.utils.diagnosis import (
    AgentDiagnosisError,
    HistoryPersistenceError,
    InvalidLogError,
    diagnose_and_persist,
)
from app.utils.validation import validate_diagnosis

logger = logging.getLogger(__name__)
diagnosis_bp = Blueprint("diagnosis", __name__)


@diagnosis_bp.route("/diagnose", methods=["POST"])
def diagnose():
    """Accept a CI/CD failure log, run the agent, and return a diagnosis."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "log" not in data:
        return jsonify({"error": "Missing 'log' field in request body"}), 400

    try:
        diagnosis = diagnose_and_persist(
            current_app._get_current_object(),
            data["log"],
            source="manual_upload",
        )
    except InvalidLogError as exc:
        return jsonify({"error": str(exc)}), 413 if "exceeds" in str(exc) else 400
    except AgentDiagnosisError as exc:
        logger.exception("Diagnosis agent failed: %s", exc)
        return jsonify({"error": str(exc)}), 502
    except HistoryPersistenceError as exc:
        logger.exception("Could not persist diagnosis history: %s", exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify(diagnosis), 200


@diagnosis_bp.route("/verify", methods=["POST"])
def verify():
    """Ask an independent AI reviewer whether a diagnosis is correct."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "log" not in data or "diagnosis" not in data:
        return jsonify({"error": "'log' and 'diagnosis' are required"}), 400
    raw_log = data["log"]
    proposed = data["diagnosis"]
    if not isinstance(raw_log, str) or not raw_log.strip():
        return jsonify({"error": "'log' must be a non-empty string"}), 400
    if len(raw_log) > current_app.config["MAX_LOG_CHARS"]:
        return jsonify({"error": "'log' exceeds the configured size limit"}), 413
    if not isinstance(proposed, dict):
        return jsonify({"error": "'diagnosis' must be an object"}), 400

    try:
        proposed = validate_diagnosis(proposed)
    except (TypeError, ValueError):
        return jsonify({"error": "'diagnosis' does not match the diagnosis schema"}), 400

    try:
        result = current_app.extensions["diagnosis_verifier"].verify(raw_log, proposed)
    except VerificationError as exc:
        logger.exception("AI verification failed: %s", exc)
        return jsonify({"error": str(exc)}), 503
    return jsonify(result), 200


@diagnosis_bp.route("/history", methods=["GET"])
def history():
    """Return paginated diagnosis history with optional filters."""
    try:
        page = int(request.args.get("page", 1))
        page_size_arg = request.args.get("page_size")
        page_size = int(page_size_arg) if page_size_arg is not None else None
        category = request.args.get("category") or None
        payload = current_app.extensions["history_store"].list(
            page=page,
            page_size=page_size,
            category=category,
            from_date=request.args.get("from"),
            to_date=request.args.get("to"),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(payload), 200


@diagnosis_bp.route("/knowledge-base/stats", methods=["GET"])
def kb_stats():
    """Return knowledge-base statistics."""
    store = current_app.extensions["vector_store"]
    return jsonify({"chunk_count": store.count()}), 200
