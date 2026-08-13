"""Generate safe local synthetic CI/CD failure logs.

The generator creates failures from known templates rather than executing
arbitrary commands. Ground-truth metadata is created before any optional AI
rewriting and is never used as diagnosis input.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

CATEGORIES = (
    "dependency_error",
    "docker_failure",
    "test_failure",
    "iac_misconfiguration",
)


@dataclass(frozen=True)
class GeneratedFailure:
    """A generated log and its evaluation-only metadata."""

    log_id: str
    raw_log: str
    category: str
    description: str
    expected_root_cause: str
    remediation_keywords: list[str]
    ai_variation_requested: bool
    ai_variation_applied: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "log_id": self.log_id,
            "source": "local_generated",
            "log": self.raw_log,
            "ground_truth": {
                "category": self.category,
                "description": self.description,
                "expected_root_cause": self.expected_root_cause,
                "remediation_keywords": self.remediation_keywords,
            },
            "ai_variation_requested": self.ai_variation_requested,
            "ai_variation_applied": self.ai_variation_applied,
        }


_TEMPLATES: dict[str, tuple[dict[str, Any], ...]] = {
    "dependency_error": (
        {
            "description": "pip dependency resolution conflict",
            "packages": ("requests", "urllib3"),
            "body": (
                "Run python -m pip install -r requirements.txt\n"
                "ERROR: Cannot install {package_a}=={version_a} and "
                "{package_b}=={version_b} because these package versions have "
                "conflicting dependencies.\n"
                "The selected package requires {package_b}<3, but the requirements "
                "file pins {package_b}=={version_b}.\n"
                "ERROR: ResolutionImpossible: dependency resolution failed.\n"
                "Error: Process completed with exit code 1."
            ),
            "root_cause": "The requirements file pins incompatible package versions, so pip cannot resolve the dependency graph.",
            "keywords": ["requirements.txt", "compatible", "dependency", "pin"],
        },
        {
            "description": "missing Python package",
            "packages": ("acme_reporting",),
            "body": (
                "Run python -m pytest\n"
                "Traceback (most recent call last):\n"
                "  File \"tests/test_reporting.py\", line 2, in <module>\n"
                "    import {package_a}\n"
                "ModuleNotFoundError: No module named '{package_a}'\n"
                "ERROR: package is not installed in the CI environment.\n"
                "Error: Process completed with exit code 1."
            ),
            "root_cause": "The test imports a package that is not installed by the CI dependency step.",
            "keywords": ["requirements.txt", "install", "package", "dependency"],
        },
    ),
    "docker_failure": (
        {
            "description": "Docker base image tag is unavailable",
            "images": (("python", "3.99-slim"), ("node", "99-alpine")),
            "body": (
                "Run docker build -t sample-service .\n"
                "[+] Building 1.4s (2/2) FINISHED\n"
                " => ERROR [internal] load metadata for docker.io/library/{image}:"
                "{tag}\n"
                "ERROR: pull access denied for {image}, repository does not exist "
                "or may require authorization\n"
                "Error response from daemon: manifest unknown: image tag was not found\n"
                "Error: Process completed with exit code 1."
            ),
            "root_cause": "The Dockerfile references a base-image tag that is unavailable in the registry.",
            "keywords": ["Dockerfile", "FROM", "image", "tag", "registry"],
        },
        {
            "description": "Dockerfile instruction syntax error",
            "images": (("python", "3.11-slim"),),
            "body": (
                "Run docker build -t sample-service .\n"
                "Step 2/3 : RUN python --version\n"
                " ---> Running in ci-builder\n"
                "Dockerfile parse error: unknown instruction: &&\n"
                "ERROR: failed to solve: Dockerfile syntax is invalid\n"
                "Error: Process completed with exit code 1."
            ),
            "root_cause": "The Dockerfile contains an invalid continuation or instruction.",
            "keywords": ["Dockerfile", "syntax", "instruction", "RUN", "build"],
        },
    ),
    "test_failure": (
        {
            "description": "assertion regression",
            "values": ((201, 404), (expected := 0, actual := 1)),
            "body": (
                "Run python -m pytest -q\n"
                "============================= test session starts =============================\n"
                "FAILED tests/test_api.py::test_create_resource\n"
                "E   AssertionError: expected {expected}, got {actual}\n"
                "E   assert {actual} == {expected}\n"
                "=========================== short test summary info ============================\n"
                "ERROR: test suite failed.\n"
                "Error: Process completed with exit code 1."
            ),
            "root_cause": "A regression caused the actual test value to differ from the expected value.",
            "keywords": ["test", "assert", "expected", "actual", "pytest"],
        },
        {
            "description": "required CI environment variable is missing",
            "body": (
                "Run python tests/test_configuration.py\n"
                "Traceback (most recent call last):\n"
                "  File \"tests/test_configuration.py\", line 8, in <module>\n"
                "    api_key = os.environ['CI_REQUIRED_API_KEY']\n"
                "KeyError: 'CI_REQUIRED_API_KEY'\n"
                "ERROR: required environment variable is missing from CI.\n"
                "Error: Process completed with exit code 1."
            ),
            "root_cause": "The CI workflow does not provide the required CI_REQUIRED_API_KEY environment variable.",
            "keywords": ["CI_REQUIRED_API_KEY", "environment", "secret", "env"],
        },
    ),
    "iac_misconfiguration": (
        {
            "description": "invalid Terraform configuration",
            "body": (
                "Run terraform validate\n"
                "╷\n│ Error: Missing newline after argument\n"
                "│\n│   on main.tf line 8, in resource \"null_resource\" \"broken\":\n"
                "│    8:     invalid_value =\n"
                "│\n│ An argument or block definition is required here.\n╵\n"
                "ERROR: Terraform configuration is invalid.\n"
                "Error: Process completed with exit code 1."
            ),
            "root_cause": "The Terraform configuration contains an incomplete attribute assignment and invalid HCL.",
            "keywords": ["terraform", "validate", "HCL", "main.tf", "configuration"],
        },
        {
            "description": "invalid Kubernetes manifest field",
            "body": (
                "Run kubectl apply --dry-run=client --validate=strict -f deployment.yaml\n"
                "error: error validating data: ValidationError(Deployment.spec.template.spec.containers[0]): "
                "unknown field 'pullSecrets' in io.k8s.api.core.v1.Container\n"
                "ERROR: Kubernetes manifest validation failed.\n"
                "Error: Process completed with exit code 1."
            ),
            "root_cause": "The Kubernetes manifest places pullSecrets under a container instead of the pod specification.",
            "keywords": ["pullSecrets", "manifest", "kubectl", "validate", "Kubernetes"],
        },
    ),
}


def supported_categories() -> tuple[str, ...]:
    """Return categories accepted by the local generator."""
    return CATEGORIES


def _base_log(category: str, rng: random.Random) -> tuple[str, dict[str, Any]]:
    template = rng.choice(_TEMPLATES[category])
    values: dict[str, Any] = {}
    if "packages" in template:
        values["package_a"] = template["packages"][0]
        values["package_b"] = template["packages"][-1]
        values["version_a"] = rng.choice(("2.31.0", "1.4.2", "8.1.0"))
        values["version_b"] = rng.choice(("3.0.0", "1.20", "99.0.0"))
    if "images" in template:
        image, tag = rng.choice(template["images"])
        values.update(image=image, tag=tag)
    if "values" in template:
        expected, actual = rng.choice(template["values"])
        values.update(expected=expected, actual=actual)

    body = template["body"].format(**values)
    metadata = {
        "description": template["description"],
        "expected_root_cause": template["root_cause"],
        "remediation_keywords": list(template["keywords"]),
        "facts": [str(value) for value in values.values()],
    }
    return body, metadata


def _ai_rewrite(raw_log: str, metadata: dict[str, Any], api_key: str | None, model: str | None) -> str | None:
    """Ask the configured model to vary wording while preserving failure facts."""
    if not api_key:
        logger.warning("AI log variation requested but OPENAI_API_KEY is not configured")
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            temperature=0.7,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Rewrite a synthetic CI/CD failure log to sound like a GitHub Actions runner. "
                        "Preserve every technical fact, package/version, error keyword, and exit code. "
                        "Return only the log text; never add a solution or ground truth."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"log": raw_log, "facts_to_preserve": metadata["facts"]}),
                },
            ],
        )
        candidate = response.choices[0].message.content if response.choices else None
        if not candidate or any(fact and fact not in candidate for fact in metadata["facts"]):
            return None
        return candidate.strip()
    except Exception as exc:  # Optional enhancement must never break local generation.
        logger.warning("AI log variation failed; using Python template: %s", exc)
        return None


def generate_local_failure(
    category: str = "random",
    *,
    seed: int | None = None,
    use_ai_variation: bool = False,
    api_key: str | None = None,
    model: str | None = None,
) -> GeneratedFailure:
    """Generate one safe local failure log.

    ``seed`` makes Python-only generation reproducible. The optional AI step is
    deliberately best-effort and falls back to the deterministic template.
    """
    if category == "random":
        selected_category = random.Random(seed).choice(CATEGORIES)
    elif category in CATEGORIES:
        selected_category = category
    else:
        raise ValueError(f"Unsupported category {category!r}; choose random or {CATEGORIES}")

    rng = random.Random(seed)
    raw_log, metadata = _base_log(selected_category, rng)
    varied = _ai_rewrite(raw_log, metadata, api_key, model) if use_ai_variation else None
    final_log = varied or raw_log
    log_id = f"local-{hashlib.sha256(final_log.encode('utf-8')).hexdigest()[:16]}"
    return GeneratedFailure(
        log_id=log_id,
        raw_log=final_log,
        category=selected_category,
        description=metadata["description"],
        expected_root_cause=metadata["expected_root_cause"],
        remediation_keywords=metadata["remediation_keywords"],
        ai_variation_requested=use_ai_variation,
        ai_variation_applied=varied is not None,
    )
