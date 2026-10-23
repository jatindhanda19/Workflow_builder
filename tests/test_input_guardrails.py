"""Tests for input guardrails."""

import pytest

from app.security.input_guardrails import (
    MAX_INPUT_LENGTH,
    validate_input,
)


def test_accepts_normal_message():
    result = validate_input(
        "Create a workflow every Monday."
    )

    assert result.allowed is True
    assert result.reason is None
    assert result.sanitized_text == "Create a workflow every Monday."
    assert result.contains_pii is False
    assert result.original_text == ""


def test_redacts_email():
    result = validate_input(
        "Send a report to user@example.com"
    )

    assert result.allowed is True
    assert result.contains_pii is True
    assert "user@example.com" not in result.sanitized_text
    assert "[EMAIL]" in result.sanitized_text
    assert result.original_text == ""


def test_redacts_phone_number():
    result = validate_input(
        "Call 9876543210"
    )

    assert result.allowed is True
    assert result.contains_pii is True
    assert "9876543210" not in result.sanitized_text
    assert "[PHONE]" in result.sanitized_text


def test_rejects_empty_input():
    result = validate_input("   ")

    assert result.allowed is False
    assert result.reason == "Input cannot be empty."


def test_rejects_non_string_input():
    result = validate_input(None)

    assert result.allowed is False
    assert result.reason == "Input must be a string."


def test_rejects_oversized_input():
    result = validate_input("a" * (MAX_INPUT_LENGTH + 1))

    assert result.allowed is False
    assert "character limit" in result.reason
    assert result.original_text == ""
    assert result.sanitized_text == ""


def test_accepts_input_at_maximum_length():
    text = "a" * MAX_INPUT_LENGTH

    result = validate_input(text)

    assert result.allowed is True
    assert result.sanitized_text == text


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "user@example.com",
        "9876543210",
        "ABCDE1234F",
        "1234 5678 9012",
    ],
)
def test_sensitive_values_are_redacted(sensitive_value):
    result = validate_input(
        f"Please process this identifier: {sensitive_value}"
    )

    assert result.allowed is True
    assert result.contains_pii is True
    assert sensitive_value not in result.sanitized_text
    assert result.original_text == ""