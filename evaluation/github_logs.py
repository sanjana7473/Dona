"""Load realistic failure logs downloaded from the GitHub Actions workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.scenarios import FailureScenario

_REQUIRED_METADATA = {
    "scenario_id",
    "category",
    "description",
    "expected_root_cause",
    "remediation_keywords",
}


def load_github_artifacts(root: str | Path) -> list[FailureScenario]:
    """Load ``ground_truth.json`` and matching ``failure-*.log`` files.

    The expected directory structure is the one produced by ``gh run download``::

        downloaded/
          failure-log-DEP-GHA-001/
            failure-DEP-GHA-001.log
            ground_truth.json
    """
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"GitHub artifact directory does not exist: {root_path}")

    scenarios: list[FailureScenario] = []
    for metadata_path in sorted(root_path.rglob("ground_truth.json")):
        metadata: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
        missing = _REQUIRED_METADATA - metadata.keys()
        if missing:
            raise ValueError(
                f"{metadata_path} is missing metadata fields: {sorted(missing)}"
            )

        log_files = sorted(metadata_path.parent.glob("failure-*.log"))
        if len(log_files) != 1:
            raise ValueError(
                f"Expected exactly one failure log beside {metadata_path}, "
                f"found {len(log_files)}"
            )

        raw_log = log_files[0].read_text(encoding="utf-8")
        if not raw_log.strip():
            raise ValueError(f"Failure log is empty: {log_files[0]}")

        scenarios.append({
            "id": str(metadata["scenario_id"]),
            "category": str(metadata["category"]),
            "description": str(metadata["description"]),
            "raw_log": raw_log,
            "expected_root_cause": str(metadata["expected_root_cause"]),
            "remediation_keywords": [
                str(keyword) for keyword in metadata["remediation_keywords"]
            ],
            "source": "github_actions_synthetic",
        })

    if not scenarios:
        raise ValueError(f"No GitHub Actions artifacts found under {root_path}")
    return scenarios
