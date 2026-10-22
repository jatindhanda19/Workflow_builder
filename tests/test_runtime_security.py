import pytest

from app.security.runtime_guardrails import (
    sanitize_runtime_text,
    validate_http_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/api",
        "http://archive.test/invoices",
    ],
)
def test_public_http_urls_are_allowed(url):
    result = validate_http_url(url)

    assert result.allowed is True
    assert result.reason is None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not-a-url",
        "ftp://example.com/file",
        "http:///missing-host",
        "http://localhost/admin",
        "http://service.local/path",
        "http://127.0.0.1/admin",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://user:password@example.com/",
    ],
)
def test_unsafe_or_invalid_urls_are_rejected(url):
    result = validate_http_url(url)

    assert result.allowed is False
    assert result.reason


def test_runtime_error_redacts_email():
    result = sanitize_runtime_text(
        "Request failed for user@example.com"
    )

    assert "user@example.com" not in result
    assert "[EMAIL]" in result


def test_runtime_error_redacts_known_secret():
    secret = "test-secret-token-123"

    result = sanitize_runtime_text(
        f"Request failed with token {secret}",
        secrets=(secret,),
    )

    assert secret not in result
    assert "[SECRET]" in result


def test_runtime_error_redacts_secret_and_pii():
    secret = "private-token"

    result = sanitize_runtime_text(
        f"Contact user@example.com; token={secret}",
        secrets=(secret,),
    )

    assert "user@example.com" not in result
    assert secret not in result
    assert "[EMAIL]" in result
    assert "[SECRET]" in result


def test_non_string_error_is_safe():
    assert sanitize_runtime_text(None) == (
        "An unexpected runtime error occurred."
    )
