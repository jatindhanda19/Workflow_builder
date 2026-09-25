"""The only way the app talks to an LLM: strict JSON schemas, retries and logging."""

import logging
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

class LLMError(RuntimeError):
    """Raised after every retry failed; callers fall back to deterministic behaviour."""

class StructuredLLM(Protocol):
    def generate(self, schema: type[T], prompt: str, user_prompt: str) -> T: ...

@lru_cache
def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8").strip()

class LLMClient:
    def __init__(self, model: BaseChatModel, max_attempts: int = MAX_ATTEMPTS) -> None:
        self._model = model
        self._max_attempts = max_attempts

    def generate(self, schema: type[T], prompt: str, user_prompt: str) -> T:
        """Call the model with `prompt` (a file in llm/prompts) and validate the reply against `schema`."""
        runnable = self._model.with_structured_output(schema, method="function_calling")
        messages = [SystemMessage(content=load_prompt(prompt)), HumanMessage(content=user_prompt)]
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            started = time.perf_counter()
            try:
                result = runnable.invoke(messages)
                if result is None:
                    raise ValueError("model returned no structured output")
                parsed = result if isinstance(result, schema) else schema.model_validate(result)
                logger.info("llm %s ok in %.2fs (attempt %d)", prompt, time.perf_counter() - started, attempt)
                return parsed
            except Exception as exc:  # noqa: BLE001 - every failure is retried and logged
                last_error = exc
                logger.warning("llm %s attempt %d failed: %s", prompt, attempt, exc)
                if _rate_limited(exc):
                    # Retrying a quota error only burns more quota; fall back to rules straight away.
                    break
        logger.error("llm %s failed: %s", prompt, last_error)
        raise LLMError(f"{schema.__name__} generation failed: {last_error}") from last_error


def _rate_limited(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 429 or "rate limit" in str(exc).lower()


def build_llm(settings: Settings) -> LLMClient:
    if settings.provider == "groq":
        from langchain_groq import ChatGroq

        model: BaseChatModel = ChatGroq(model=settings.model, api_key=settings.api_key, temperature=settings.temperature)
    else:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(model=settings.model, api_key=settings.api_key, temperature=settings.temperature)
    return LLMClient(model)
