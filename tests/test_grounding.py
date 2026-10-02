"""Grounding matches whole words: a value is kept only if the user wrote those words, not parts of longer ones."""

import pytest
from test_clarification import _run

from app.pipeline.merge import _app_named, _grounded, _mentioned
from app.pipeline.planner_schema import PlannedParam, PlannedStep, TurnPlan
from app.state.models import WorkflowState


@pytest.mark.parametrize("value, user_text", [
    ("Git", "when a github issue is opened, create a jira ticket"),
    ("sales", "add the lead to salesforce"),
    ("500", "if the order total is above 5000"),
    ("Jo", "email john about it"),
    ("a@x.com", "send it to aa@x.com"),
    ("x.com", "send it to aa@x.com"),
    ("Sheets", "post it to the worksheet"),
])
def test_part_of_a_longer_word_is_not_grounded(value, user_text):
    assert not _mentioned(value, user_text)
    assert not _grounded(value, user_text)


@pytest.mark.parametrize("value, user_text", [
    ("6pm", "every day at 6 pm"),
    ("6 pm", "every day at 6pm"),
    ("₹500", "if the amount is above 500"),
    ("500", "if the amount is above ₹500"),
    ("100000", "if the amount is above ₹1,00,000"),
    ("#sales", "post it in #sales"),
    ("#sales", "post it in the sales channel"),
    ("john@acme.com", "email John@Acme.com when it is done."),
    ("> 5000", "if order total > 5000"),
])
def test_same_words_written_differently_are_grounded(value, user_text):
    assert _mentioned(value, user_text)
    assert _grounded(value, user_text)


def test_names_allow_singular_and_plural_but_evidence_is_exact():
    assert _mentioned("Google Sheets", "add a row to my google sheet")
    assert not _grounded("google sheets", "add a row to my google sheet")


def test_builtin_app_needs_evidence_and_other_apps_need_their_name():
    assert _app_named("Schedule", "every monday", "every monday at 9 am send a report")
    assert not _app_named("Git", None, "when a github issue is opened")
    assert _app_named("GitHub", None, "when a github issue is opened")


def _plan(app, channel=None, evidence=None):
    return TurnPlan(message_kind="build", steps=[
        PlannedStep(id="issue_trigger", kind="trigger", app=app, app_evidence=app, operation="New issue"),
        PlannedStep(id="post", kind="action", app="Slack", app_evidence="Slack", operation="Send message",
                    params=[PlannedParam(name="channel", label="Channel", kind="channel", value=channel,
                                         evidence=evidence)]),
    ])


def test_app_written_inside_another_word_is_asked_not_stored():
    state = _run(WorkflowState(), "When a github issue is opened, post it to Slack.", _plan("Git"))
    assert state.step("issue_trigger").app is None
    assert any(e.field == "issue_trigger.app" and e.event == "ignored" for e in state.log)


def test_value_inside_another_word_is_not_stored_but_a_whole_word_is():
    state = _run(WorkflowState(), "When a GitHub issue is opened, post it to Slack #salesforce-alerts.",
                 _plan("GitHub", "#sales", "#sales"))
    assert state.step("post").param("channel").value is None
    state = _run(WorkflowState(), "When a GitHub issue is opened, post it to the Slack channel #sales.",
                 _plan("GitHub", "#sales", "#sales"))
    assert state.step("post").param("channel").value == "#sales"
