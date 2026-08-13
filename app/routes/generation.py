"""Local and GitHub-generated log APIs."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from app.generation.local import CATEGORIES, generate_local_failure
from app.integrations.github import GitHubGenerationService
from app.utils.diagnosis import (
    AgentDiagnosisError,
    HistoryPersistenceError,
    diagnose_and_persist,
)

generation_bp = Blueprint("generation", __name__)


def _json_body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@generation_bp.route("/generate/local", methods=["POST"])
def generate_local():
    """Generate a safe local failure log and diagnose it immediately."""
    data = _json_body()
    category = data.get("category", "random")
    use_ai_variation = data.get("use_ai_variation", False)
    if not isinstance(category, str):
        return jsonify({"error": "'category' must be a string"}), 400
    if not isinstance(use_ai_variation, bool):
        return jsonify({"error": "'use_ai_variation' must be a boolean"}), 400

    seed = data.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        return jsonify({"error": "'seed' must be an integer"}), 400

    try:
        generated = generate_local_failure(
            category,
            seed=seed,
            use_ai_variation=use_ai_variation,
            api_key=current_app.config.get("OPENAI_API_KEY"),
            model=current_app.config.get("LLM_MODEL"),
        )
        diagnosis = diagnose_and_persist(
            current_app._get_current_object(),
            generated.raw_log,
            source="local_generated",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc), "supported_categories": [*CATEGORIES]}), 400
    except AgentDiagnosisError as exc:
        return jsonify({"error": str(exc)}), 502
    except HistoryPersistenceError as exc:
        return jsonify({"error": str(exc)}), 500

    response = generated.as_dict()
    response["diagnosis"] = diagnosis
    return jsonify(response), 200


@generation_bp.route("/github/generate", methods=["POST"])
def generate_github():
    """Dispatch the configured GitHub Actions failure-generator workflow."""
    data = _json_body()
    category = data.get("category")
    if not isinstance(category, str):
        return jsonify({"error": "'category' is required and must be a string"}), 400
    if category not in CATEGORIES:
        return jsonify({
            "error": f"Unsupported category {category!r}",
            "supported_categories": [*CATEGORIES],
        }), 400
    ref = data.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref.strip()):
        return jsonify({"error": "'ref' must be a non-empty string"}), 400

    service: GitHubGenerationService = current_app.extensions["github_generation"]
    try:
        job = service.start(category=category, ref=ref)
    except ValueError as exc:
        return jsonify({"error": str(exc), "supported_categories": [*CATEGORIES]}), 400
    return jsonify(job), 202 if job.get("status") in {"queued", "dispatched"} else 503


@generation_bp.route("/github/generate/<job_id>", methods=["GET"])
def github_generation_status(job_id: str):
    """Return the current status and, when complete, the log and diagnosis."""
    service: GitHubGenerationService = current_app.extensions["github_generation"]
    job = service.get(job_id)
    if job is None:
        return jsonify({"error": "GitHub generation job not found"}), 404
    return jsonify(job), 200
