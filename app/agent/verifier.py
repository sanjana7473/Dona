"""AI-based verification of a generated diagnosis."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.utils.validation import FailureCategory

logger = logging.getLogger(__name__)


class VerificationError(RuntimeError):
    """The AI verification request failed or returned invalid output."""


class VerificationOutput(BaseModel):
    """Stable response contract for the Verify with AI button."""

    model_config = ConfigDict(extra="ignore")

    is_correct: bool
    confidence_score: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    corrected_failure_category: FailureCategory | None = None
    corrected_root_cause: str | None = None
    corrected_recommended_fix: str | None = None


_SYSTEM_PROMPT = """You are an independent CI/CD diagnosis reviewer.

Compare the original CI/CD failure log with the proposed diagnosis. Decide
whether the category, root cause, and recommended fix are technically correct.
Do not assume that the proposed diagnosis is correct. Base your decision only
on the original log and proposed diagnosis.

Return ONLY valid JSON with exactly this structure:
{
  "is_correct": true,
  "confidence_score": 0.0,
  "explanation": "Why the diagnosis is correct or incorrect.",
  "evidence": ["Specific evidence from the log."],
  "corrected_failure_category": null,
  "corrected_root_cause": null,
  "corrected_recommended_fix": null
}

If the proposed diagnosis is incorrect, provide corrected values for the three
corrected_* fields. If it is correct, leave those fields null. Valid categories
are dependency_error, docker_failure, test_failure, iac_misconfiguration, and
unknown."""


class DiagnosisVerifier:
    """Use a separate LLM call to review a diagnosis."""

    def __init__(
        self,
        *,
        llm_model: str,
        api_key: str | None,
        temperature: float = 0.0,
    ):
        self._llm = ChatOpenAI(
            model=llm_model,
            api_key=api_key,
            temperature=temperature,
        )

    @staticmethod
    def _parse_response(content: Any) -> dict[str, Any]:
        if not isinstance(content, str):
            raise VerificationError("AI verifier returned a non-text response")
        raw = content.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        candidates = [raw]
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            candidates.append(content[start:end])

        for candidate in candidates:
            try:
                return VerificationOutput.model_validate(json.loads(candidate)).model_dump(
                    exclude_none=True
                )
            except (json.JSONDecodeError, TypeError, ValidationError):
                continue
        raise VerificationError("AI verifier returned invalid JSON")

    def verify(self, raw_log: str, diagnosis: dict[str, Any]) -> dict[str, Any]:
        """Review a diagnosis without using hidden ground-truth metadata."""
        try:
            response = self._llm.invoke(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(
                        content=json.dumps(
                            {
                                "original_log": raw_log,
                                "proposed_diagnosis": diagnosis,
                            },
                            ensure_ascii=False,
                        )
                    ),
                ]
            )
            result = self._parse_response(response.content)
            result["verification_mode"] = "ai_reviewer"
            return result
        except VerificationError:
            raise
        except Exception as exc:
            logger.exception("AI verification failed: %s", exc)
            raise VerificationError("AI verification is unavailable") from exc
