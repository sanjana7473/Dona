"""Validation models for the public diagnosis response."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FailureCategory = Literal[
    "dependency_error",
    "docker_failure",
    "test_failure",
    "iac_misconfiguration",
    "unknown",
]


class DiagnosisOutput(BaseModel):
    """The stable JSON contract returned by the diagnosis API."""

    model_config = ConfigDict(extra="ignore")

    failure_category: FailureCategory
    root_cause: str = Field(min_length=1)
    confidence_score: float = Field(ge=0.0, le=1.0)
    recommended_fix: str = Field(min_length=1)
    retrieved_references: list[str]
    diagnosis_mode: str | None = None


def validate_diagnosis(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a diagnosis dictionary."""
    return DiagnosisOutput.model_validate(value).model_dump(exclude_none=True)
