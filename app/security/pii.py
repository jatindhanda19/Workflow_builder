"""
Basic PII detection and redaction.

Detects common:
- Email addresses
- Phone numbers
- Indian Aadhaar-like numbers
- Indian PAN-like identifiers

This module is a starting point, not a complete PII detector.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PIIEntity:
    """A detected sensitive entity."""

    entity_type: str
    start: int
    end: int


# Patterns are intentionally conservative, but regex detection is imperfect.
PATTERNS = {
    "EMAIL": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
    "PHONE": re.compile(
        r"(?<!\w)(?:\+?91[\s.-]?)?[6-9]\d{9}(?!\w)"
    ),
    "AADHAAR_LIKE": re.compile(
        r"(?<!\d)(?:\d{4}[\s-]?){2}\d{4}(?!\d)"
    ),
    "PAN_LIKE": re.compile(
        r"\b[A-Z]{5}\d{4}[A-Z]\b",
        re.IGNORECASE,
    ),
}


def detect_pii(text: str) -> list[PIIEntity]:
    """
    Detect supported PII patterns in text.

    Returns entity types and character positions, not the actual values.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    entities = []

    for entity_type, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            entities.append(
                PIIEntity(
                    entity_type=entity_type,
                    start=match.start(),
                    end=match.end(),
                )
            )

    # Keep results deterministic and avoid overlapping replacements.
    entities.sort(key=lambda item: (item.start, -(item.end - item.start)))

    non_overlapping = []
    last_end = -1

    for entity in entities:
        if entity.start >= last_end:
            non_overlapping.append(entity)
            last_end = entity.end

    return non_overlapping


def redact_pii(text: str) -> str:
    """
    Replace detected PII with type-labelled placeholders.

    Example:
        Contact me at user@example.com
        ->
        Contact me at [EMAIL]
    """
    entities = detect_pii(text)

    for entity in reversed(entities):
        placeholder = f"[{entity.entity_type}]"
        text = (
            text[:entity.start]
            + placeholder
            + text[entity.end:]
        )

    return text


def contains_pii(text: str) -> bool:
    """Return True when a supported PII pattern is detected."""
    return bool(detect_pii(text))