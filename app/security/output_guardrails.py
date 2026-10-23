"""
Output guardrails for the Workflow Builder.

Provides PII detection and redaction for text returned to users.
Workflow schema validation remains in app.pipeline.generation.checks.
"""

from dataclasses import dataclass

from app.security.pii import contains_pii, redact_pii


@dataclass(frozen=True)
class OutputGuardrailResult:
    """Result of checking an application output."""

    allowed: bool
    reason: str | None
    sanitized_text: str
    contains_pii: bool


def validate_output_text(text: str) -> OutputGuardrailResult:
    """
    Validate and sanitize text before displaying it to a user.

    Detected PII is redacted rather than causing the entire response
    to be rejected.

    This function is intended for text responses, not serialized
    workflow objects or executable instructions.
    """

    if not isinstance(text, str):
        return OutputGuardrailResult(
            allowed=False,
            reason="Output must be a string.",
            sanitized_text="",
            contains_pii=False,
        )

    if not text.strip():
        return OutputGuardrailResult(
            allowed=False,
            reason="Output cannot be empty.",
            sanitized_text="",
            contains_pii=False,
        )

    has_pii = contains_pii(text)

    sanitized = redact_pii(text) if has_pii else text

    return OutputGuardrailResult(
        allowed=True,
        reason=None,
        sanitized_text=sanitized,
        contains_pii=has_pii,
    )