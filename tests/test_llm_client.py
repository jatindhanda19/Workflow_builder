"""The Groq client: rejected replies that pass our own schema are used, settings load, and the turn goes on
without the model when code can handle the answer itself."""

import json

import pytest
from test_clarification import _run
from test_conditions import FIELD_ASK, INVOICE_NO_FIELD, _invoice_plan

from app.core.config import ConfigError, load_settings
from app.core.llm import LLMCall, LLMClient, RateLimitedError, build_llm
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
    def generate(self, schema, prompt, user_prompt, call=None):
        raise RateLimitedError("over quota", retry_after=60)


def test_number_typed_as_field_is_explained_even_when_the_model_is_down():
    state = _run(WorkflowState(), INVOICE_NO_FIELD, _invoice_plan(next_question=FIELD_ASK))
    state = run_turn(build_graph(DownLLM()), state, "890")
    assert state.target == "check_amount.field"
    assert "890 looks like a value, not a field name" in state.messages[-1].content
    assert "usage limit" not in state.messages[-1].content


# Seen with openai/gpt-oss-120b: the answer wrapped in the JSON schema. LangChain fails to parse it and keeps the
# reply on the error's `llm_output`; it used to cost three retries and then the fallback model.
WRAPPED_REPLIES = [
    # Every value written as a schema node.
    {"description": "", "properties": {
        "answer": {"type": ["string", "null"], "default": None},
        "message_kind": {"type": "string", "enum": ["build"], "description": "build, question, …"},
        "next_question": {"type": "object", "properties": {
            "target": {"type": "string", "enum": ["trigger.app"]},
            "question": {"type": "string", "enum": ["How would you like to start the summarization workflow?"]},
            "options": {"type": "array", "items": {"type": "string"}}}},
        "steps": {"type": "array", "items": {"type": "object"}},
        "workflow_name": {"type": "null"}}, "required": ["message_kind"]},
    # Some values plain, some schema nodes.
    {"description": "", "properties": {
        "answer": {"type": "null", "default": None}, "message_kind": {"type": "string", "enum": ["build"]},
        "next_question": {"target": "trigger.app", "question": "What should start the workflow?",
                          "options": ["When a new paper is added to a Google Drive folder", "When I click a button"]},
        "steps": [], "workflow_name": {"type": "null"}}, "required": ["message_kind"]},
    # Every value plain, inside "properties".
    {"description": "", "properties": {
        "answer": {"type": "null"}, "message_kind": "build",
        "next_question": {"target": "email_trigger.app", "question": "Which email service should I monitor?",
                          "options": ["Gmail", "Outlook"]},
        "steps": [{"id": "email_trigger", "kind": "trigger", "app": None, "app_evidence": "Email",
                   "operation": "New email", "after": [], "branch": None, "params": []}],
        "workflow_name": None}, "required": ["message_kind"]},
]


@pytest.mark.parametrize("reply", WRAPPED_REPLIES)
def test_reply_wrapped_in_the_schema_is_unwrapped_without_retrying(reply):
    from langchain_core.exceptions import OutputParserException

    model = RejectingModel(OutputParserException("Failed to parse TurnPlan", llm_output=json.dumps(reply)))
    plan = LLMClient([("m", model)]).generate(TurnPlan, "planner", "hi")
    assert model.calls == 1
    assert plan.message_kind == "build" and plan.answer is None and plan.workflow_name is None
    assert plan.next_question is not None and plan.next_question.target.endswith(".app")


def test_unwrapped_reply_keeps_its_steps_and_options():
    from langchain_core.exceptions import OutputParserException

    model = RejectingModel(OutputParserException("Failed to parse", llm_output=json.dumps(WRAPPED_REPLIES[2])))
    plan = LLMClient([("m", model)]).generate(TurnPlan, "planner", "hi")
    assert [(s.id, s.kind, s.operation) for s in plan.steps] == [("email_trigger", "trigger", "New email")]
    assert plan.next_question.options == ["Gmail", "Outlook"]


def test_unparseable_reply_that_is_not_a_wrapped_answer_is_still_retried():
    from langchain_core.exceptions import OutputParserException

    model = RejectingModel(OutputParserException("Failed to parse", llm_output='{"properties": {"steps": 5}}'))
    with pytest.raises(Exception, match="generation failed"):
        LLMClient([("m", model)], max_attempts=2).generate(TurnPlan, "planner", "hi")
    assert model.calls == 2


class AnsweringModel:
    """Returns a parsed reply, as LangChain does when nothing went wrong."""

    def __init__(self, plan):
        self.plan = plan

    def with_structured_output(self, schema, method):
        return self

    def invoke(self, messages):
        return self.plan


def _log_lines(monkeypatch):
    from app.core.llm import client

    lines = []
    monkeypatch.setattr(client.logger, "info", lambda msg, *args: lines.append(("info", msg % args)))
    monkeypatch.setattr(client.logger, "warning", lambda msg, *args: lines.append(("warning", msg % args)))
    return lines


def test_unwrapping_is_recorded_and_logged_as_our_parser_not_the_provider(monkeypatch):
    from langchain_core.exceptions import OutputParserException

    lines = _log_lines(monkeypatch)
    call = LLMCall()
    model = RejectingModel(OutputParserException("Failed to parse", llm_output=json.dumps(WRAPPED_REPLIES[0])))
    LLMClient([("m", model)]).generate(TurnPlan, "planner", "hi", call=call)
    assert call.unwrapped is True
    assert ("info", "llm planner with m: planner output was schema-wrapped; unwrapped and validated locally") in lines
    assert not any("provider rejected" in text for _, text in lines)


def test_a_reply_the_provider_rejected_keeps_its_own_log_and_is_not_marked_unwrapped(monkeypatch):
    lines = _log_lines(monkeypatch)
    call = LLMCall()
    model = RejectingModel(ToolUseFailed(json.dumps({"name": "TurnPlan", "arguments": REPLY})))
    LLMClient([("m", model)]).generate(TurnPlan, "planner", "hi", call=call)
    assert call.unwrapped is False
    assert any(level == "warning" and "the provider rejected the reply" in text for level, text in lines)


def test_a_normal_reply_passes_through_unchanged():
    plan, call = TurnPlan(message_kind="build"), LLMCall()
    assert LLMClient([("m", AnsweringModel(plan))]).generate(TurnPlan, "planner", "hi", call=call) is plan
    assert call.unwrapped is False


def test_the_turn_log_says_when_the_reply_was_unwrapped():
    from app.core.turnlog import turn_record

    class UnwrappingLLM:
        def generate(self, schema, prompt, user_prompt, call=None):
            call.unwrapped = True
            return TurnPlan(message_kind="build")

    before = WorkflowState()
    after = run_turn(build_graph(UnwrappingLLM()), before, "Every Monday send a report")
    assert turn_record("s1", before, after, latency_ms=1)["unwrapped"] is True
    plain = run_turn(build_graph(DownLLM()), before, "Every Monday send a report")
    assert turn_record("s1", before, plain, latency_ms=1)["unwrapped"] is False
