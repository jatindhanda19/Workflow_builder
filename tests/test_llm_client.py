"""The Groq client: rejected replies that pass our own schema are used, settings load, and the turn goes on
without the model when code can handle the answer itself."""

import json

import pytest
from test_clarification import _run
from test_conditions import FIELD_ASK, INVOICE_NO_FIELD, _invoice_plan

from app.core.config import ConfigError, load_settings
from app.core.llm import LLMClient, RateLimitedError, build_llm
from app.pipeline.graph import build_graph, run_turn
from app.pipeline.planner_schema import TurnPlan
from app.state.models import WorkflowState

REPLY = {"message_kind": "build", "steps": [
    {"id": "trigger", "kind": "trigger", "app": "Shopify", "operation": "New order"},
    {"id": "check", "kind": "condition", "app": "If", "operation": "Check", "after": "trigger", "branch": None,
     "params": [{"name": "field", "label": "Order value field", "kind": "field", "value": 890, "evidence": 890}]},
    {"id": "notify", "kind": "action", "app": "Slack", "operation": "Send message", "after": ["check"], "branch": True,
     "params": [{"name": "fields", "label": "Fields", "kind": "list", "value": ["order id", "total"]}]},
]}


class ToolUseFailed(Exception):
    """Shaped like groq.BadRequestError: the parsed error body is on `.body`."""

    def __init__(self, failed_generation):
        super().__init__("Error code: 400 - tool call validation failed")
        self.body = {"message": "tool call validation failed: /steps/1/params/0/value: expected string",
                     "code": "tool_use_failed", "failed_generation": failed_generation}


class RejectingModel:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def with_structured_output(self, schema, method):
        return self

    def invoke(self, messages):
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("tool, arguments", [
    ("TurnPlan", REPLY),
    # Seen with openai/gpt-oss-20b: "attempted to call tool 'functions.turnPlan' which was not in request.tools".
    ("functions.turnPlan", json.dumps(REPLY)),
])
def test_rejected_reply_that_passes_our_schema_is_used(tool, arguments):
    model = RejectingModel(ToolUseFailed(json.dumps({"name": tool, "arguments": arguments})))
    plan = LLMClient([("m", model)]).generate(TurnPlan, "planner", "hi")
    assert model.calls == 1
    check, notify = plan.steps[1], plan.steps[2]
    assert check.params[0].value == "890" and check.after == ["trigger"]
    assert notify.branch == "true" and notify.params[0].value == "order id, total"


def test_rejected_reply_that_fails_our_schema_is_still_an_error():
    model = RejectingModel(ToolUseFailed(json.dumps({"name": "TurnPlan", "arguments": {"steps": "nonsense"}})))
    with pytest.raises(Exception, match="tool call validation failed"):
        LLMClient([("m", model)], max_attempts=2).generate(TurnPlan, "planner", "hi")
    assert model.calls == 2


def test_settings(monkeypatch):
    monkeypatch.setattr("app.core.config.load_dotenv", lambda: None)
    for name in ("LLM_MODEL", "LLM_FALLBACK_MODEL", "LLM_REASONING_EFFORT"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        load_settings()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    settings = load_settings()
    assert (settings.model, settings.fallback_model, settings.reasoning_effort) == (
        "openai/gpt-oss-120b", "openai/gpt-oss-20b", "low")
    assert isinstance(build_llm(settings), LLMClient)


class DownLLM:
    def generate(self, schema, prompt, user_prompt):
        raise RateLimitedError("over quota", retry_after=60)


def test_number_typed_as_field_is_explained_even_when_the_model_is_down():
    state = _run(WorkflowState(), INVOICE_NO_FIELD, _invoice_plan(next_question=FIELD_ASK))
    state = run_turn(build_graph(DownLLM()), state, "890")
    assert state.target == "check_amount.field"
    assert "890 looks like a value, not a field name" in state.messages[-1].content
    assert "usage limit" not in state.messages[-1].content
