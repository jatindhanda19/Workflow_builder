"""Test harness: a scripted LLM so the deterministic pipeline can be tested offline.

The fake returns prepared IntentResult / Extraction / FollowUpDecision objects per
user message and refuses to reword questions, so template wording is used.
Everything else (planning, validation, consistency checks, question order,
readiness, generation, diagram) runs for real. Every turn also asserts that the
generic error text never appears unless a test explicitly allows it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.extraction.extract import LATEST_MARKER  # noqa: E402
from app.pipeline.extraction.schema import Extraction  # noqa: E402
from app.pipeline.graph import GENERIC_ERROR, build_graph, run_turn  # noqa: E402
from app.pipeline.intent.schema import FollowUpDecision, IntentResult  # noqa: E402
from app.core.llm import LLMError  # noqa: E402
from app.state.models import WorkflowState  # noqa: E402

MARKERS = {IntentResult: "USER REQUEST:", Extraction: LATEST_MARKER, FollowUpDecision: "USER MESSAGE:"}


def v(field: str, value: str, evidence: str | None = None) -> dict:
    return {"field": field, "value": value, "evidence": evidence or value}


def amb(field: str, phrase: str, reason: str = "unclear") -> dict:
    return {"field": field, "phrase": phrase, "reason": reason}


class ScriptedLLM:
    def __init__(self) -> None:
        self.scripts: dict[type, dict[str, dict]] = {IntentResult: {}, Extraction: {}, FollowUpDecision: {}}
        self.failing: set[str] = set()

    def generate(self, schema, prompt: str, user_prompt: str):
        if schema not in MARKERS:
            raise LLMError("phrasing disabled in tests")
        message = user_prompt.split(MARKERS[schema], 1)[1].strip()
        if message in self.failing:
            raise LLMError("simulated failure after retries")
        if schema is FollowUpDecision and message not in self.scripts[schema]:
            raise LLMError("no scripted follow-up")
        return schema.model_validate(self.scripts[schema].get(message, {}))


class Conversation:
    def __init__(self, llm: ScriptedLLM) -> None:
        self.llm = llm
        self.graph = build_graph(llm)
        self.state = WorkflowState()

    def say(self, message: str, extraction: dict | None = None, intent: dict | None = None,
            followup: dict | None = None, allow_error: bool = False) -> WorkflowState:
        for schema, script in ((Extraction, extraction), (IntentResult, intent), (FollowUpDecision, followup)):
            if script is not None:
                self.llm.scripts[schema][message] = script
        self.state = run_turn(self.graph, self.state, message)
        if not allow_error:
            assert GENERIC_ERROR not in self.reply, f"generic error shown for {message!r}"
        return self.state

    @property
    def reply(self) -> str:
        return self.state.messages[-1].content

    @property
    def asking(self) -> str | None:
        return self.state.target_field if self.state.mode == "collecting" else None

    def value(self, key: str):
        return self.state.values().get(key)

    def asked(self) -> list[str]:
        return [e.field for e in self.state.log if e.event == "asked"]


@pytest.fixture
def convo() -> Conversation:
    return Conversation(ScriptedLLM())
