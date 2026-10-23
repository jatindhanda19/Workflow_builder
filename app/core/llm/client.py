"""The only way the app talks to an LLM: Groq through LangChain, with Groq's Structured Outputs (a JSON schema),
retries, a fallback model and logging. Code then grounds and validates every value the model returns."""

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, ValidationError

from app.core.config import Settings

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)
PROMPTS = Path(__file__).parent / "prompts"
# Per model: the request, then one repair request that shows the model its rejected reply and why. Repeating the
# identical request mostly repeats the same mistake, so after the repair the next model is tried.
MAX_ATTEMPTS = 2
REPAIR_CHARS = 1500  # how much of the rejected reply the repair request quotes
DEFAULT_COOLDOWN_SECONDS = 60.0
# Keys of a JSON schema node; a dict made only of these is a schema, not a value (see _unschema).
SCHEMA_KEYS = frozenset({"type", "default", "enum", "description", "properties", "required", "items", "title",
                         "anyOf", "additionalProperties"})
RETRY_IN = re.compile(r"try again in ((?:\d+h)?(?:\d+m)?(?:[\d.]+s)?)", re.IGNORECASE)


class LLMError(RuntimeError):
    """Raised after every retry failed; callers fall back to deterministic behaviour."""


class RateLimitedError(LLMError):
    """Every model is over its quota. `retry_after` is in seconds, when the provider said."""

    def __init__(self, message: str, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after

    @property
    def wait_text(self) -> str:
        if not self.retry_after:
            return "a few minutes"
        minutes = math.ceil(self.retry_after / 60)
        return "about a minute" if minutes <= 1 else f"about {minutes} minutes"


@dataclass
class LLMCall:
    """How one generate() call went, for the turn log. The caller passes a fresh one, so calls on the shared client
    from different requests never mix their details."""

    unwrapped: bool = False  # the reply came wrapped in its JSON schema and was unwrapped in code


class StructuredLLM(Protocol):
    def generate(self, schema: type[T], prompt: str, user_prompt: str, call: LLMCall | None = None) -> T: ...


@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8").strip()


class LLMClient:
    """Tries each model in order. A model over its quota is skipped until the provider's wait time is over."""

    def __init__(self, models: list[tuple[str, BaseChatModel]], max_attempts: int = MAX_ATTEMPTS,
                 strict: bool = False) -> None:
        self._models = models
        self._max_attempts = max_attempts
        # Groq's strict Structured Outputs: the provider checks the reply against the full schema and returns it in
        # the error ("failed_generation") when it does not match, so _recover can still use what passes our schema.
        self._strict = strict
        self._cooldown_until: dict[str, float] = {}

    def generate(self, schema: type[T], prompt: str, user_prompt: str, call: LLMCall | None = None) -> T:
        """Call a model with `prompt` (a file in llm/prompts) and validate the reply against `schema`. Details of
        the call are recorded in `call`, if given."""
        call = call if call is not None else LLMCall()
        messages = [SystemMessage(content=load_prompt(prompt)), HumanMessage(content=user_prompt)]
        last_error: Exception | None = None
        waits: list[float] = []
        for name, model in self._models:
            remaining = self._cooldown_until.get(name, 0) - time.monotonic()
            if remaining > 0:
                waits.append(remaining)
                continue
            try:
                return self._generate(name, model, schema, prompt, messages, call)
            except _RateLimited as exc:
                wait = exc.retry_after or DEFAULT_COOLDOWN_SECONDS
                self._cooldown_until[name] = time.monotonic() + wait
                waits.append(wait)
                logger.warning("llm %s: %s is rate limited for %.0fs, trying the next model", prompt, name, wait)
                last_error = exc.__cause__
            except LLMError as exc:
                last_error = exc
        if waits and len(waits) == len(self._models):
            raise RateLimitedError(f"every model is rate limited: {last_error}", min(waits)) from last_error
        raise LLMError(f"{schema.__name__} generation failed: {last_error}") from last_error

    def _generate(self, name: str, model: BaseChatModel, schema: type[T], prompt: str, messages: list,
                  call: LLMCall) -> T:
        # Groq's Structured Outputs: the reply is JSON for the schema, not a tool call, so the model cannot skip
        # the call or misname the tool.
        options = {"strict": True} if self._strict else {}
        runnable = model.with_structured_output(schema, method="json_schema", **options)
        last_error: Exception | None = None
        request = messages
        for attempt in range(1, self._max_attempts + 1):
            started = time.perf_counter()
            try:
                result = runnable.invoke(request)
                if result is None:
                    raise ValueError("model returned no structured output")
                parsed = result if isinstance(result, schema) else schema.model_validate(result)
                logger.info("llm %s ok with %s in %.2fs (attempt %d)", prompt, name, time.perf_counter() - started, attempt)
                return parsed
            except Exception as exc:  # noqa: BLE001 - every failure is retried and logged
                if _rate_limited(exc):
                    # Retrying a quota error only burns more quota.
                    raise _RateLimited(_retry_after(exc)) from exc
                recovered = _recover(exc, schema)
                if recovered is not None:
                    parsed, unwrapped = recovered
                    if unwrapped:
                        # The provider answered 200; it was our parser that rejected the schema-shaped reply.
                        call.unwrapped = True
                        logger.info("llm %s with %s: planner output was schema-wrapped; unwrapped and validated "
                                    "locally", prompt, name)
                    elif _error_body(exc).get("failed_generation"):
                        logger.warning("llm %s with %s: the provider rejected the reply (%s); it passed our own "
                                       "schema", prompt, name, _reason(exc))
                    else:
                        logger.warning("llm %s with %s: the reply failed to parse (%s); it passed our own schema",
                                       prompt, name, _reason(exc))
                    return parsed
                last_error = exc
                logger.warning("llm %s attempt %d with %s failed: %s", prompt, attempt, name, _reason(exc))
                request = _repair_request(messages, exc)  # the next attempt shows the model what was wrong
        logger.error("llm %s failed with %s: %s", prompt, name, last_error)
        raise LLMError(f"{schema.__name__} generation failed: {last_error}") from last_error


def build_llm(settings: Settings) -> LLMClient:
    names = [settings.model, *([settings.fallback_model] if settings.fallback_model else [])]
    return LLMClient([(name, ChatGroq(model=name, api_key=settings.api_key, temperature=settings.temperature,
                                      reasoning_effort=settings.reasoning_effort))
                      for name in names], strict=settings.strict_output)


def _repair_request(messages: list, exc: Exception) -> list:
    """The original request plus what went wrong: the model's rejected reply (when the error carries it) and the
    reason, so the second attempt can fix the reply instead of repeating it."""
    rejected = _error_body(exc).get("failed_generation") or getattr(exc, "llm_output", None)
    note = f"Your last reply could not be used: {_reason(exc)}"
    if isinstance(rejected, str) and rejected.strip():
        note += f"\n\nYour reply was:\n{rejected.strip()[:REPAIR_CHARS]}"
    note += "\n\nReply again with the corrected JSON only, matching the schema exactly."
    return [*messages, HumanMessage(content=note)]


class _RateLimited(Exception):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


def _error_body(exc: Exception) -> dict:
    body = getattr(exc, "body", None)
    body = body.get("error", body) if isinstance(body, dict) else None
    return body if isinstance(body, dict) else {}


def _recover(exc: Exception, schema: type[T]) -> tuple[T, bool] | None:
    """A reply that was rejected but still holds a usable answer:
    - Groq checks a tool call against the JSON schema strictly and rejects small slips (890 for "890"); its error
      carries the model's reply ("failed_generation").
    - LangChain could not parse the reply (`llm_output`), e.g. when gpt-oss-120b wraps its answer in the schema:
      {"description": "", "properties": {"message_kind": "build", …}, "required": […]}.
    If the reply, unwrapped, passes our own schema, it is returned with whether it was schema-wrapped. Grounding
    and validation still decide every value, as for any reply."""
    raw = _error_body(exc).get("failed_generation") or getattr(exc, "llm_output", None)
    if not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("arguments"), (dict, str)):  # {"name": …, "arguments": …}
            data = data["arguments"]
            data = json.loads(data) if isinstance(data, str) else data
        wrapped = isinstance(data, dict) and isinstance(data.get("properties"), dict)
        if wrapped:
            data = _unschema(data)
        return schema.model_validate(data), wrapped
    except (ValueError, ValidationError):
        return None


def _unschema(value):
    """The values of a reply written as a JSON schema: {"properties": {…}} is its values, {"enum": ["build"]} is
    "build", {"type": "null"} or {"default": null} is None. A schema node with no value ({"type": "array"}) is left
    out, so the field gets its default. Anything else is kept as it is."""
    if isinstance(value, list):
        return [v for v in map(_unschema, value) if v is not _NO_VALUE]
    if not isinstance(value, dict):
        return value
    if value and value.keys() <= SCHEMA_KEYS:
        if isinstance(value.get("properties"), dict):
            return _values(value["properties"])
        if "default" in value:
            return value["default"]
        enum = value.get("enum")
        if isinstance(enum, list) and len(enum) == 1:
            return enum[0]
        return None if value.get("type") == "null" else _NO_VALUE
    return _values(value)


def _values(fields: dict) -> dict:
    return {k: v for k, v in ((k, _unschema(v)) for k, v in fields.items()) if v is not _NO_VALUE}


_NO_VALUE = object()


def _reason(exc: Exception) -> str:
    """The provider's own message without the echoed reply, which can be thousands of characters."""
    message = _error_body(exc).get("message")
    return str(message) if message else str(exc)[:500]


def _rate_limited(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 429 or "rate limit" in str(exc).lower()


def _retry_after(exc: Exception) -> float | None:
    """Seconds from "Please try again in 9m18.144s", if the provider said."""
    match = RETRY_IN.search(str(exc))
    if not match or not match.group(1):
        return None
    parts = {unit: value for value, unit in re.findall(r"([\d.]+)([hms])", match.group(1))}
    return float(parts.get("h", 0)) * 3600 + float(parts.get("m", 0)) * 60 + float(parts.get("s", 0)) or None
