"""A step's app is asked before its settings, whatever the planner predicts. Session c51ddcc2: the planner asked
for the model of a summarize step before its app, and the user later picked Google Sheets for it."""

from test_clarification import _run

from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.state.models import WorkflowState

REQUEST = "When a record is added in Airtable, summarize it and append it to Google Sheets"


def _plan(next_target, summarize_app=None, model=None):
    return TurnPlan(message_kind="build", workflow_name="Summaries to Sheets",
                    next_question=NextQuestion(target=next_target, question="Which one?", options=["gpt-4o", "gpt-4o-mini"]),
                    steps=[
        PlannedStep(id="airtable_trigger", kind="trigger", app="Airtable", app_evidence="Airtable",
                    operation="New record"),
        # The planner's "OpenAI" is not kept: the user did not name it.
        PlannedStep(id="summarize", kind="action", app=summarize_app, app_evidence=summarize_app,
                    operation="Summarize", params=[
            PlannedParam(name="model", label="Model", value=model, evidence=model),
            PlannedParam(name="prompt", label="Prompt")]),
        PlannedStep(id="append_row", kind="action", app="Google Sheets", app_evidence="Google Sheets",
                    operation="Append row"),
    ])


def test_settings_of_a_step_without_an_app_are_not_asked_first():
    state = _run(WorkflowState(), REQUEST, _plan("summarize.model", summarize_app="OpenAI"))
    assert state.step("summarize").app is None
    assert state.target == "summarize.app"
    assert state.question.text != "Which one?"  # the planner's model question is not used for the app


def test_settings_are_asked_once_the_app_is_known():
    state = _run(WorkflowState(), REQUEST, _plan("summarize.model"))
    state = _run(state, "OpenAI", _plan("summarize.model", summarize_app="OpenAI"))
    assert state.step("summarize").app == "OpenAI"
    assert state.target == "summarize.model"
    assert [o.label for o in state.question.options][:2] == ["gpt-4o", "gpt-4o-mini"]


def test_a_value_the_user_gave_before_the_app_is_kept_while_the_app_is_asked():
    state = _run(WorkflowState(), REQUEST + " with gpt-4o", _plan("summarize.prompt", model="gpt-4o"))
    assert state.target == "summarize.app"
    assert state.step("summarize").param("model").value == "gpt-4o"
