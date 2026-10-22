import pytest

from app.security.output_guardrails import validate_output_text


def test_normal_output_is_allowed():
    result = validate_output_text("Your workflow is ready.")

    assert result.allowed is True
    assert result.reason is None
    assert result.sanitized_text == "Your workflow is ready."
    assert result.contains_pii is False


def test_email_is_redacted():
    result = validate_output_text(
        "The configured email is sakshi@example.com"
    )

    assert result.allowed is True
    assert result.contains_pii is True
    assert "sakshi@example.com" not in result.sanitized_text


def test_phone_number_is_redacted():
    result = validate_output_text(
        "Contact number: 9876543210"
    )

    assert result.allowed is True
    assert result.contains_pii is True
    assert "9876543210" not in result.sanitized_text


def test_empty_output_is_rejected():
    result = validate_output_text("   ")

    assert result.allowed is False
    assert result.sanitized_text == ""


def test_non_string_output_is_rejected():
    result = validate_output_text(None)

    assert result.allowed is False
    assert result.sanitized_text == ""


@pytest.mark.parametrize(
    ("text", "original_value", "placeholder"),
    [
        ("Email: user@example.com", "user@example.com", "[EMAIL]"),
        ("Phone: 9876543210", "9876543210", "[PHONE]"),
    ],
)
def test_detected_pii_does_not_remain_in_output(
    text, original_value, placeholder
):
    result = validate_output_text(text)

    assert result.allowed is True
    assert result.contains_pii is True
    assert original_value not in result.sanitized_text
    assert placeholder in result.sanitized_text