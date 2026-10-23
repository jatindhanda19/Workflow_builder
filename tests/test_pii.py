import pytest

from app.security.pii import (
    contains_pii,
    detect_pii,
    redact_pii,
)


def test_detect_email():
    entities = detect_pii("Contact user@example.com")

    assert any(entity.entity_type == "EMAIL" for entity in entities)


def test_redact_email():
    result = redact_pii("Contact user@example.com")

    assert "user@example.com" not in result
    assert "[EMAIL]" in result


def test_detect_indian_phone():
    entities = detect_pii("Call 9876543210")

    assert any(entity.entity_type == "PHONE" for entity in entities)


def test_redact_phone():
    result = redact_pii("Call 9876543210")

    assert "9876543210" not in result
    assert "[PHONE]" in result


def test_detect_pan_like_identifier():
    entities = detect_pii("Identifier: ABCDE1234F")

    assert any(entity.entity_type == "PAN_LIKE" for entity in entities)


def test_redact_pan_like_identifier():
    result = redact_pii("Identifier: ABCDE1234F")

    assert "ABCDE1234F" not in result
    assert "[PAN_LIKE]" in result


def test_detect_aadhaar_like_number():
    entities = detect_pii("Identifier: 1234 5678 9012")

    assert any(
        entity.entity_type == "AADHAAR_LIKE"
        for entity in entities
    )


def test_no_pii():
    text = "Create a workflow that runs every Monday"

    assert detect_pii(text) == []
    assert redact_pii(text) == text
    assert not contains_pii(text)


def test_invalid_input():
    with pytest.raises(TypeError):
        detect_pii(None)


def test_redact_multiple_entities():
    text = "Email user@example.com and call 9876543210"
    result = redact_pii(text)

    assert "user@example.com" not in result
    assert "9876543210" not in result
    assert "[EMAIL]" in result
    assert "[PHONE]" in result