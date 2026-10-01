import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_MODELS = {"groq": "openai/gpt-oss-120b", "openai":"gpt-4o-mini"}
API_KEYS_VARS = {'groq':"GROQ_API_KEY", "openai": "OPENAI_API_KEY"}
# Used when the main model hits a rate limit. Groq limits each model separately, so a second model keeps working.
DEFAULT_FALLBACK_MODELS = {"groq": "openai/gpt-oss-20b", "openai": ""}

class ConfigError(RuntimeError):
    pass

@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    api_key: str
    temperature: float
    fallback_model: str | None = None

def load_settings() -> Settings:
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER","groq").strip().lower()
    if provider not in DEFAULT_MODELS:
        raise ConfigError(f"LLM_PROVIDER must be one of: {','.join(DEFAULT_MODELS)}")

    key_var = API_KEYS_VARS[provider]
    api_key = os.getenv(key_var, "").strip()
    if not api_key:
        raise ConfigError(f"{key_var} is not set. Copy .env.example to .env and add your key.")

    model = os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODELS[provider]
    fallback = os.getenv("LLM_FALLBACK_MODEL", "").strip() or DEFAULT_FALLBACK_MODELS[provider]
    return Settings(
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
        fallback_model=None if fallback.lower() in ("", "none", model.lower()) else fallback,
    )


