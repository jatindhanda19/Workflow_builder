from functools import lru_cache
from pathlib import Path


@lru_cache
def load_prompt(name: str) -> str:
    return (Path(__file__).parent / f"{name}.txt").read_text(encoding="utf-8").strip()
