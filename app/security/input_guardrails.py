"""
Input guardrails for the workflow builder.

Detects common PII patterns and supports optional preservation
of email addresses needed by workflow operations.
"""

from dataclasses import dataclass

from app.security.pii import contains_pii, detect_pii, redact_pii


MAX_INPUT_LENGTH = 10_000


@dataclass(frozen=True)
class InputGuardrailResult:
    """Result returned by input validation."""

    allowed: bool
    reason: str | None
    original_text: str
    sanitized_text: str
    contains_pii: bool


def validate_input(
    text: str,
    *,
    preserve_emails: bool = False,
) -> InputGuardrailResult:
    """
    Validate a user message.

    By default, detected PII is redacted.
    If preserve_emails=True, email addresses are retained for
    legitimate workflow operations, while other detected PII
    remains redacted.

    Callers should avoid logging original_text or unsanitized input.
    """

    if not isinstance(text, str):
        return InputGuardrailResult(
            allowed=False,
            reason="Input must be a string.",
            original_text="",
            sanitized_text="",
            contains_pii=False,
        )

    if not text.strip():
        return InputGuardrailResult(
            allowed=False,
            reason="Input cannot be empty.",
            original_text="",
            sanitized_text="",
            contains_pii=False,
        )

    if len(text) > MAX_INPUT_LENGTH:
        return InputGuardrailResult(
            allowed=False,
            reason=f"Input exceeds the {MAX_INPUT_LENGTH}-character limit.",
            original_text="",
            sanitized_text="",
            contains_pii=False,
        )

    has_pii = contains_pii(text)

    if not has_pii:
        sanitized = text
    elif not preserve_emails:
        sanitized = redact_pii(text)
    else:
        # Redact all supported PII except email addresses.
        entities = detect_pii(text)
        replacements = []

        for entity in entities:
            if entity.entity_type == "EMAIL":
                continue

            replacements.append(
                (
                    entity.start,
                    entity.end,
                    f"[{entity.entity_type}]",
                )
            )

        sanitized = text

        for start, end, placeholder in reversed(replacements):
            sanitized = (
                sanitized[:start]
                + placeholder
                + sanitized[end:]
            )

    return InputGuardrailResult(
        allowed=True,
        reason=None,
        original_text="",
        sanitized_text=sanitized,
        contains_pii=has_pii,
    )
