"""Phase 2: the planner, the merge (validation), the questions, the LLM client and the runner use the app catalogue.
The LLM is scripted; nothing here calls Groq."""

import json

import pytest
from auth_test_utils import authenticate_test_client
from fastapi.testclient import TestClient
from test_clarification import _run

import app.main as main
from app.catalogue import CATALOGUE
from app.core.config import load_settings
from app.core.llm import LLMCall, LLMClient
from app.pipeline.planner import build_context
from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.runtime.executor import RUNNERS, runner_for
from app.state.models import ACTION_PLACEHOLDER, WorkflowState
from app.state.store import SessionStore

REQUEST = "When a webhook is called, post the order to Slack #sales"


def _p(name, value=None, kind="text", evidence=None):
    return PlannedParam(name=name, label=name.capitalize(), kind=kind, value=value, evidence=evidence or value)


def _plan(*action_params, app="Slack", operation="Send message", next_question=None):
    return TurnPlan(message_kind="build", next_question=next_question, steps=[
        PlannedStep(id="trigger", kind="trigger", app="Webhook", app_evidence="webhook", operation="On webhook call"),
        PlannedStep(id="notify", kind="action", app=app, app_evidence=app, operation=operation,
                    params=list(action_params)),
    ])


# -- planner -------------------------------------------------------------------------------------------------
def test_the_planner_sees_the_catalogue_with_exact_setting_names():
    context = build_context(WorkflowState(latest_user_message=REQUEST))
    assert "APP CATALOGUE" in context
    assert "- Slack / Send message (action): channel* (channel), message* (long_text)" in context
    assert "- Google Sheets / Append row (action): spreadsheet* (text), sheet* (text), row* (list)" in context
    assert "catalogue_key" not in context  # code's bookkeeping is not shown to the planner


# -- merge: catalogue operations are checked strictly --------------------------------------------------------
def test_a_catalogue_step_keeps_only_its_settings_under_their_names_and_kinds():
    state = _run(WorkflowState(), REQUEST + ' saying "New order"',
                 _plan(_p("channel", "#sales"), _p("text", "New order"), _p("footer", "Sent by bot")))
    notify = state.step("notify")
    assert notify.catalogue_key == "slack.send_message" and notify.verified
    assert [(p.name, p.kind, p.value) for p in notify.params] == [
        ("channel", "channel", "#sales"), ("message", "long_text", "New order")]
    ignored = [e.detail for e in state.log if e.event == "ignored"]
    assert '"footer" is not a setting of Slack: Send message' in ignored


def test_a_missing_required_setting_is_asked_not_generated():
    state = _run(WorkflowState(), REQUEST, _plan(_p("channel", "#sales")))
    assert state.workflow is None
    assert state.target == "notify.message"
    assert state.question.text == "What should the message be (The message text)?"


def test_operation_aliases_and_app_spellings_match_the_catalogue():
    state = _run(WorkflowState(), "When a webhook is called, add a row to my google sheet",
                 _plan(app="google sheet", operation="Add row"))
    step = state.step("notify")
    assert (step.app, step.catalogue_key) == ("Google Sheets", "google_sheets.append_row")
    assert {p.name for p in step.params} == {"spreadsheet", "sheet", "row"}  # all required, so all asked


def test_a_value_kept_under_an_alias_survives_the_next_turn():
    state = _run(WorkflowState(), REQUEST + ' saying "New order"', _plan(_p("text", "New order")))
    state = _run(state, "#sales", _plan(_p("channel", "#sales"), _p("message")))
    assert state.step("notify").param("message").value == "New order"


def test_a_value_is_checked_again_under_the_catalogue_kind():
    state = _run(WorkflowState(), "When a webhook is called, email boss@example.com with Gmail",
                 _plan(_p("to", "boss@example.com", kind="text"), app="Gmail", operation="Send email"))
    to = state.step("notify").param("to")
    assert to.kind == "email_list" and to.value == ["boss@example.com"]


def test_apps_and_operations_outside_the_catalogue_stay_unverified():
    state = _run(WorkflowState(), "When a webhook is called, send it to Telegram",
                 _plan(_p("chat_id", "12345", evidence="12345"), app="Telegram"))
    telegram = state.step("notify")
    assert telegram.catalogue_key is None and not telegram.verified
    assert [p.name for p in telegram.params] == ["chat_id"]  # kept as the planner described it
    sheets = _run(WorkflowState(), "When a webhook is called, read new rows in Google Sheets",
                  _plan(app="Google Sheets", operation="Get new rows")).step("notify")
    assert sheets.app == "Google Sheets" and sheets.catalogue_key is None  # a real operation, just not catalogued


def test_a_picked_app_gets_its_required_settings_in_the_same_turn():
    ask_app = NextQuestion(target="notify.app", question="Which app?", options=["Slack", "Gmail"])
    state = _run(WorkflowState(), "When a webhook is called, notify me",
                 _plan(app=None, operation="Send message", next_question=ask_app))
    assert state.target == "notify.app"
    state = _run(state, "A", _plan(app=None, operation="Send message"))
    assert state.step("notify").catalogue_key == "slack.send_message"
    assert state.workflow is None and state.target == "notify.channel"


def test_a_null_operation_does_not_invalidate_the_plan_and_is_asked():
    plan = TurnPlan.model_validate({"message_kind": "build", "steps": [
        {"id": "trigger", "kind": "trigger", "app": "Webhook", "app_evidence": "webhook", "operation": "On webhook call"},
        {"id": "notify", "kind": "action", "app": "Slack", "app_evidence": "Slack", "operation": None}]})
    assert plan.steps[1].operation == ""
    state = _run(WorkflowState(), REQUEST, plan)
    assert state.target == "notify.operation"


# -- questions ----------------------------------------------------------------------------------------------
def test_app_options_are_catalogue_apps_or_apps_the_user_named():
    ask = NextQuestion(target="notify.app", question="Which app should notify you?",
                       options=["HubSpot", "Telegram", "Slack", "Microsoft Teams"])
    state = _run(WorkflowState(), "When a webhook is called, notify me on Slack or Telegram",
                 _plan(app=None, next_question=ask))
    assert [o.label for o in state.question.options] == ["Telegram", "Slack"]
    plain = _run(WorkflowState(), "When a webhook is called, notify me",
                 _plan(app=None, next_question=NextQuestion(target="notify.app", question="Which app?", options=[])))
    assert [o.label for o in plain.question.options] == ["Gmail", "Slack", "Google Sheets", "HTTP Request"]


# -- LLM client: strict outputs and one repair attempt -------------------------------------------------------
class RecordingModel:
    """Fails with a parse error carrying the bad reply, then answers; records what it was sent."""

    def __init__(self, *results):
        self.results, self.requests, self.options = list(results), [], None

    def with_structured_output(self, schema, method, **options):
        self.options = options
        return self

    def invoke(self, messages):
        self.requests.append(messages)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _parse_error(raw):
    from langchain_core.exceptions import OutputParserException

    return OutputParserException("operation: Input should be a valid string", llm_output=raw)


def test_strict_outputs_are_requested_when_enabled():
    model = RecordingModel(TurnPlan(message_kind="build"))
    LLMClient([("m", model)], strict=True).generate(TurnPlan, "planner", "hi")
    assert model.options == {"strict": True}
    model = RecordingModel(TurnPlan(message_kind="build"))
    LLMClient([("m", model)]).generate(TurnPlan, "planner", "hi")
    assert model.options == {}


def test_the_second_attempt_shows_the_model_its_error_and_reply():
    bad = '{"message_kind": "build", "steps": [{"id": "a", "kind": "action"}]}'
    model = RecordingModel(_parse_error(bad), TurnPlan(message_kind="other"))
    plan = LLMClient([("m", model)]).generate(TurnPlan, "planner", "hi", call=LLMCall())
    assert plan.message_kind == "other" and len(model.requests) == 2
    first, second = model.requests
    assert len(second) == len(first) + 1
    repair = second[-1].content
    assert "operation: Input should be a valid string" in repair and bad in repair
    assert "corrected JSON" in repair


def test_after_one_repair_the_next_model_is_tried():
    first = RecordingModel(_parse_error("{}"), _parse_error("{}"))
    fallback = RecordingModel(TurnPlan(message_kind="build"))
    LLMClient([("main", first), ("fallback", fallback)]).generate(TurnPlan, "planner", "hi")
    assert len(first.requests) == 2 and len(fallback.requests) == 1


def test_strict_output_setting(monkeypatch):
    monkeypatch.setattr("app.core.config.load_dotenv", lambda: None)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.delenv("LLM_STRICT_OUTPUT", raising=False)
    assert load_settings().strict_output is True
    monkeypatch.setenv("LLM_STRICT_OUTPUT", "false")
    assert load_settings().strict_output is False


# -- runner -------------------------------------------------------------------------------------------------
def test_runners_cover_exactly_the_executable_catalogue_entries():
    assert set(RUNNERS) == {key for key, entry in CATALOGUE.items() if entry.executable}


@pytest.mark.parametrize("app, operation, kind, runner", [
    ("Slack", "Post message", "action", "slack"),
    ("Webhook", "On webhook call", "trigger", "trigger"),
    ("HTTP Request", "POST", "action", "http"),
    ("If", "Check amount", "condition", "if"),
    ("Gmail", "Send email", "action", None),  # in the catalogue, not executable
    ("Schedule", "On a schedule", "trigger", None),
    ("Slack", "Notify the team", "action", "slack"),  # outside the catalogue: the earlier matching still applies
    ("Telegram", "Send message", "action", None),
])
def test_what_runs_is_decided_by_the_catalogue(app, operation, kind, runner):
    assert runner_for(app, operation, kind) == runner


def test_steps_say_whether_they_are_verified_and_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", SessionStore(tmp_path / "sessions.db"))
    client = TestClient(main.app)
    owner = authenticate_test_client(client)["id"]
    state = _run(WorkflowState(), "When a webhook is called, email boss@example.com with Gmail and tell Telegram",
                 TurnPlan(message_kind="build", steps=[
                     PlannedStep(id="trigger", kind="trigger", app="Webhook", app_evidence="webhook",
                                 operation="On webhook call"),
                     PlannedStep(id="mail", kind="action", app="Gmail", app_evidence="Gmail", operation="Send email"),
                     PlannedStep(id="tell", kind="action", app="Telegram", app_evidence="Telegram",
                                 operation=ACTION_PLACEHOLDER),
                 ]))
    main.store.save("s1", state, owner_id=owner)
    steps = {s["id"]: s for s in client.get("/sessions/s1").json()["steps"]}
    assert (steps["trigger"]["verified"], steps["trigger"]["executable"]) == (True, True)
    assert (steps["mail"]["catalogue_key"], steps["mail"]["verified"], steps["mail"]["executable"]) == (
        "gmail.send_email", True, False)
    assert (steps["tell"]["verified"], steps["tell"]["executable"]) == (False, False)
    json.dumps(steps)
