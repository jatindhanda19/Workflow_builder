"""
Runtime security guardrails.

Provides HTTP URL validation and sanitization of runtime error messages.
These checks complement, rather than replace, the runtime executor.
"""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.security.pii import redact_pii


@dataclass(frozen=True)
class RuntimeGuardrailResult:
    allowed: bool
    reason: str | None = None


def validate_http_url(url: str) -> RuntimeGuardrailResult:
    """
    Validate an HTTP(S) destination before making a request.

    Rejects:
    - Non-HTTP(S) schemes.
    - Missing or malformed hostnames.
    - URLs containing embedded username/password credentials.
    - Localhost and local-only hostnames.
    - Literal IP addresses that are not globally routable.

    Note: hostname DNS resolution and redirect destinations require
    additional protections at the actual HTTP-request layer.
    """

    if not isinstance(url, str) or not url.strip():
        return RuntimeGuardrailResult(False, "The URL is missing.")

    try:
        parsed = urlsplit(url.strip())

        if parsed.scheme.lower() not in {"http", "https"}:
            return RuntimeGuardrailResult(
                False, "Only HTTP and HTTPS URLs are allowed."
            )

        if not parsed.hostname:
            return RuntimeGuardrailResult(
                False, "The URL must contain a hostname."
            )

        if parsed.username is not None or parsed.password is not None:
            return RuntimeGuardrailResult(
                False, "URLs containing embedded credentials are not allowed."
            )

        # Accessing .port also validates malformed port numbers.
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return RuntimeGuardrailResult(
                False, "The URL contains an invalid port."
            )

        hostname = parsed.hostname.rstrip(".").lower()

        if (
            hostname == "localhost"
            or hostname.endswith(".localhost")
            or hostname.endswith(".local")
            or hostname.endswith(".internal")
        ):
            return RuntimeGuardrailResult(
                False, "Local-only hostnames are not allowed."
            )

        # Reject literal IP addresses that are not globally routable.
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None

        if address is not None and not address.is_global:
            return RuntimeGuardrailResult(
                False, "Private or non-public IP addresses are not allowed."
            )

    except (ValueError, UnicodeError):
        return RuntimeGuardrailResult(
            False, "The URL is malformed."
        )

    return RuntimeGuardrailResult(True)


def sanitize_runtime_text(
    text: str,
    secrets: tuple[str, ...] = (),
) -> str:
    """
    Remove known secret values and redact detected PII from error text.

    Use this for messages shown to users, not for values sent to external
    services or for internal workflow data.
    """

    if not isinstance(text, str):
        return "An unexpected runtime error occurred."

    sanitized = text

    # Replace longer secrets first to avoid partial replacements.
    for secret in sorted(
        (value for value in secrets if isinstance(value, str) and value),
        key=len,
        reverse=True,
    ):
        sanitized = sanitized.replace(secret, "[SECRET]")

    # Redact email addresses, phone numbers, and other supported PII.
    sanitized = redact_pii(sanitized)

    return sanitized
