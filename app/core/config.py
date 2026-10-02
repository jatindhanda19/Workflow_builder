import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_MODEL = "openai/gpt-oss-120b"
# Groq limits each model separately, so when the main model is rate limited this one keeps working.
DEFAULT_FALLBACK_MODEL = "openai/gpt-oss-20b"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    fallback_model: str | None
    temperature: float
    # gpt-oss models think before answering. "low" keeps turns fast and cheap; with more, gpt-oss-20b often spends
    # its whole reply on reasoning and returns no JSON. None: not sent (for models without this setting).
    reasoning_effort: str | None


def load_settings() -> Settings:
    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
    model = os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL
    fallback = os.getenv("LLM_FALLBACK_MODEL", "").strip() or DEFAULT_FALLBACK_MODEL
    reasoning = os.getenv("LLM_REASONING_EFFORT", "").strip().lower() or "low"
    return Settings(
        api_key=api_key,
        model=model,
        fallback_model=None if fallback.lower() in ("none", model.lower()) else fallback,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
        reasoning_effort=None if reasoning == "none" else reasoning,
    )
