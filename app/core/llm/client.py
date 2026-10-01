"""The only way the app talks to an LLM: strict JSON schemas, retries, a fallback model and logging."""

import logging
import math
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Protocol, TypeVar
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.config import Settings

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)
PROMPTS = Path(__file__).parent / "prompts"
MAX_ATTEMPTS = 3
DEFAULT_COOLDOWN_SECONDS = 60.0
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


class StructuredLLM(Protocol):
    def generate(self, schema: type[T], prompt: str, user_prompt: str) -> T: ...


@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8").strip()


class LLMClient:
    """Tries each model in order. A model over its quota is skipped until the provider's wait time is over."""

    def __init__(self, models: list[tuple[str, BaseChatModel]], max_attempts: int = MAX_ATTEMPTS) -> None:
        self._models = models
        self._max_attempts = max_attempts
        self._cooldown_until: dict[str, float] = {}

    def generate(self, schema: type[T], prompt: str, user_prompt: str) -> T:
        """Call a model with `prompt` (a file in llm/prompts) and validate the reply against `schema`."""
        messages = [SystemMessage(content=load_prompt(prompt)), HumanMessage(content=user_prompt)]
        last_error: Exception | None = None
        waits: list[float] = []
        for name, model in self._models:
            remaining = self._cooldown_until.get(name, 0) - time.monotonic()
            if remaining > 0:
                waits.append(remaining)
                continue
            try:
                return self._generate(name, model, schema, prompt, messages)
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

    def _generate(self, name: str, model: BaseChatModel, schema: type[T], prompt: str, messages: list) -> T:
        runnable = model.with_structured_output(schema, method="function_calling")
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            started = time.perf_counter()
            try:
                result = runnable.invoke(messages)
                if result is None:
                    raise ValueError("model returned no structured output")
                parsed = result if isinstance(result, schema) else schema.model_validate(result)
                logger.info("llm %s ok with %s in %.2fs (attempt %d)", prompt, name, time.perf_counter() - started, attempt)
                return parsed
            except Exception as exc:  # noqa: BLE001 - every failure is retried and logged
                if _rate_limited(exc):
                    # Retrying a quota error only burns more quota.
                    raise _RateLimited(_retry_after(exc)) from exc
                last_error = exc
                logger.warning("llm %s attempt %d with %s failed: %s", prompt, attempt, name, exc)
        logger.error("llm %s failed with %s: %s", prompt, name, last_error)
        raise LLMError(f"{schema.__name__} generation failed: {last_error}") from last_error


class _RateLimited(Exception):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


def _rate_limited(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 429 or "rate limit" in str(exc).lower()


def _retry_after(exc: Exception) -> float | None:
    """Seconds from "Please try again in 9m18.144s", if the provider said."""
    match = RETRY_IN.search(str(exc))
    if not match or not match.group(1):
        return None
    parts = {unit: value for value, unit in re.findall(r"([\d.]+)([hms])", match.group(1))}
    return float(parts.get("h", 0)) * 3600 + float(parts.get("m", 0)) * 60 + float(parts.get("s", 0)) or None


def build_llm(settings: Settings) -> LLMClient:
    names = [settings.model, *([settings.fallback_model] if settings.fallback_model else [])]
    return LLMClient([(name, _chat_model(settings, name)) for name in names])


def _chat_model(settings: Settings, name: str) -> BaseChatModel:
    if settings.provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=name, api_key=settings.api_key, temperature=settings.temperature)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=name, api_key=settings.api_key, temperature=settings.temperature)
