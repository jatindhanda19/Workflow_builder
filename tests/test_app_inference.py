"""An app the user named is not asked again when the planner leaves it null, empty placeholder steps from the
planner are dropped, and answers already kept survive a sparser plan (such as one from the fallback model)."""

from test_clarification import _run

from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.state.models import WorkflowState


def _trigger(step_id="email_trigger", evidence=None, app=None):
    return PlannedStep(id=step_id, kind="trigger", app=app, app_evidence=evidence, operation="On new email")


def _notify(app="Slack", channel=None):
    return PlannedStep(id="notify", kind="action", app=app, app_evidence=app, operation="Send message",
                       params=[PlannedParam(name="channel", label="Channel", kind="channel", value=channel,
                                            evidence=channel)])


def _plan(*steps, target=None):
    question = NextQuestion(target=target, question="Which one?") if target else None
    return TurnPlan(message_kind="build", steps=list(steps), next_question=question)


def test_app_named_in_the_evidence_is_kept_without_asking():
    state = _run(WorkflowState(), "When I get an email in Gmail, post it to Slack",
                 _plan(_trigger(evidence="Gmail"), _notify(), target="email_trigger.app"))
    assert state.step("email_trigger").app == "Gmail"
    assert state.target == "notify.channel"  # the next open item, not the app again


def test_session_d056_gmail_from_the_step_id_and_the_placeholder_step_is_dropped():
    # The fallback model's plan for the reply "Gmail": no app, no evidence, and a "next_step" with operation "?".
    plan = _plan(_trigger("gmail_trigger"),
                 PlannedStep(id="next_step", kind="action", operation="?", after=["gmail_trigger"]),
                 target="gmail_trigger.app")
    state = _run(WorkflowState(), "Gmail", plan)
    assert state.step("gmail_trigger").app == "Gmail"
    assert state.step("next_step") is None
    assert state.target != "gmail_trigger.app"


def test_words_that_name_no_app_are_still_asked():
    state = _run(WorkflowState(), "When an email arrives, post it to Slack",
                 _plan(_trigger(evidence="Email"), _notify(), target="email_trigger.app"))
    assert state.step("email_trigger").app is None and state.target == "email_trigger.app"


def test_evidence_naming_two_apps_is_still_asked():
    state = _run(WorkflowState(), "When an email arrives in Gmail or Outlook, post it to Slack",
                 _plan(_trigger(evidence="Gmail or Outlook"), _notify(), target="email_trigger.app"))
    assert state.step("email_trigger").app is None


def test_an_app_the_user_did_not_name_is_not_taken_from_the_evidence():
    state = _run(WorkflowState(), "When an email arrives, post it to Slack",
                 _plan(_trigger("gmail_trigger", evidence="Gmail"), _notify(), target="gmail_trigger.app"))
    assert state.step("gmail_trigger").app is None


def test_other_names_for_an_app_count():
    state = _run(WorkflowState(), "When a mail arrives in Google Mail, post it to Slack",
                 _plan(_trigger(evidence="Google Mail"), _notify()))
    assert state.step("email_trigger").app == "Gmail"


def test_kept_answers_survive_a_sparser_plan_from_the_fallback_model():
    state = _run(WorkflowState(), "When I get an email in Gmail, post it to Slack #alerts",
                 _plan(_trigger(app="Gmail", evidence="Gmail"), _notify(channel="#alerts")))
    assert (state.step("email_trigger").app, state.step("notify").param("channel").value) == ("Gmail", "#alerts")

    # The fallback model returns the same steps without their apps or values, or no steps at all.
    for sparse in (_plan(_trigger(), PlannedStep(id="notify", kind="action", operation="Send message")), _plan()):
        after = _run(state, "thanks", sparse)
        assert after.step("email_trigger").app == "Gmail"
        assert after.step("notify").app == "Slack"
        assert after.step("notify").param("channel").value == "#alerts"
