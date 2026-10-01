"""Ambiguous messages are clarified, never guessed. The LLM is scripted, so these test the code path only."""

from app.core.llm import RateLimitedError
from app.pipeline.graph import build_graph, run_turn
from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.state.models import WorkflowState

REQUEST = "When a form is submitted, add the response to Airtable and email the person."
DETAILS = ("The form is Contact form, the Airtable base is Leads and the table is Responses. "
           "Email subject: Thanks for reaching out. Message: We got your response and will reply soon.")


class ScriptedLLM:
    def __init__(self, *plans: TurnPlan) -> None:
        self.plans = list(plans)

    def generate(self, schema, prompt, user_prompt):
        return self.plans.pop(0)


def _p(name, kind="text", value=None, evidence=None):
    return PlannedParam(name=name, label=name.replace("_", " ").capitalize(), kind=kind, value=value, evidence=evidence)


def _plan(filled=False, email_app="Gmail", next_question=None, kind="build"):
    v = (lambda value, evidence: {"value": value, "evidence": evidence}) if filled else (lambda *_: {})
    return TurnPlan(message_kind=kind, workflow_name="Form to Airtable and email", next_question=next_question, steps=[
        PlannedStep(id="form_trigger", kind="trigger", app="Google Forms", app_evidence="a form is submitted",
                    operation="On form submission", params=[_p("form", **v("Contact form", "Contact form"))]),
        PlannedStep(id="add_airtable_record", kind="action", app="Airtable", app_evidence="Airtable",
                    operation="Create record", params=[_p("base", **v("Leads", "base is Leads")),
                                                       _p("table", **v("Responses", "table is Responses"))]),
        PlannedStep(id="send_email", kind="action", app=email_app, app_evidence="Gmail" if email_app else None,
                    operation="Send email", params=[
                        _p("to", "email", **v("{{form_trigger.email}}", "email the person")),
                        _p("subject", **v("Thanks for reaching out", "Thanks for reaching out")),
                        _p("message", "long_text", **v("We got your response and will reply soon",
                                                       "We got your response and will reply soon"))]),
    ])


def _run(state, message, *plans):
    return run_turn(build_graph(ScriptedLLM(*plans)), state, message)


def _generated() -> WorkflowState:
    state = _run(WorkflowState(), REQUEST, _plan(email_app=None))
    return _run(state, DETAILS + " Use Google Forms and Gmail.", _plan(filled=True))


def _snapshot(state):
    return [(s.id, s.app, [(p.name, p.value) for p in s.params]) for s in state.steps]


def test_1_request_asks_for_missing_configuration():
    state = _run(WorkflowState(), REQUEST, _plan(email_app=None, next_question=NextQuestion(
        target="form_trigger.app", question="Which app is the form in?", options=["Google Forms", "Typeform"])))
    # "a form" does not name an app, so the form app is asked first; Airtable was named and is kept.
    assert [s.app for s in state.steps] == [None, "Airtable", None]
    assert state.target == "form_trigger.app" and state.workflow is None


def test_2_unclear_message_is_clarified_not_merged():
    state = _generated()
    before, workflow = _snapshot(state), state.workflow
    # Even if the LLM also proposes an Airtable mapping, nothing from an unclear turn is merged.
    guess = _plan(filled=True, kind="unclear", next_question=NextQuestion(
        target="clarify", question="Where should I add the message: to the Airtable record, the confirmation email, or both?",
        options=["Add it to the Airtable record", "Add it to the confirmation email", "Add it to both"]))
    guess.steps[1].params.append(_p("note", value="small message", evidence="small message"))
    state = _run(state, "also add small message", guess)
    assert _snapshot(state) == before and state.workflow == workflow
    assert state.target == "clarify"
    assert "Airtable record, the confirmation email, or both" in state.messages[-1].content
    assert [o.label for o in state.question.options] == [
        "Add it to the Airtable record", "Add it to the confirmation email", "Add it to both"]


def test_2b_picked_clarification_continues_normally():
    state = _generated()
    state = _run(state, "also add small message", TurnPlan(message_kind="unclear", next_question=NextQuestion(
        target="clarify", question="Where should I add the message?",
        options=["Add it to the Airtable record", "Add it to the confirmation email"])))
    plan = _plan(filled=True, next_question=NextQuestion(target="send_email.footer", question="What should the note say?"))
    plan.steps[2].params.append(_p("footer", "long_text"))
    state = _run(state, "B", plan)
    assert state.picked == "Add it to the confirmation email"
    # The generated workflow stays while the new message text is asked.
    assert state.target == "send_email.footer" and state.workflow is not None


def test_3_send_it_to_the_team_asks_for_destination():
    state = _generated()
    state = _run(state, "send it to the team", TurnPlan(message_kind="unclear", next_question=NextQuestion(
        target="clarify", question="Which email address or team should receive it?", options=[])))
    assert state.target == "clarify"
    assert "Which email address or team should receive it?" in state.messages[-1].content
    # The LLM gave no options, so the steps are offered; no address is invented.
    assert all("@" not in o.label for o in state.question.options)


def test_3b_unusable_clarification_falls_back_to_a_plain_question():
    state = _generated()
    state = _run(state, "save the details", TurnPlan(message_kind="unclear"))
    assert state.target == "clarify" and "Which part of the workflow" in state.question.text


def test_4_use_gmail_answers_the_app_question():
    state = _run(WorkflowState(), REQUEST, _plan(email_app=None, next_question=NextQuestion(
        target="send_email.app", question="Which app should send the email?", options=["Gmail", "Outlook"])))
    state.target = "send_email.app"
    state = _run(state, "Use Gmail", _plan())
    assert state.step("send_email").app == "Gmail"


def test_5_and_6_complete_details_generate_forms_airtable_gmail():
    state = _run(WorkflowState(), REQUEST + " " + DETAILS + " Use Google Forms and Gmail.", _plan(filled=True))
    assert state.question is None and state.workflow is not None
    nodes = state.workflow.nodes
    assert [n.parameters["app"] for n in nodes] == ["Google Forms", "Airtable", "Gmail"]
    assert [(e.source, e.target) for e in state.workflow.edges] == [
        ("form_trigger", "add_airtable_record"), ("add_airtable_record", "send_email")]
    assert nodes[2].parameters["to"] == "{{form_trigger.email}}"


class RateLimitedLLM:
    def generate(self, schema, prompt, user_prompt):
        raise RateLimitedError("over quota", retry_after=1260)


def test_rate_limited_turn_shows_only_the_error():
    state = _run(WorkflowState(), REQUEST, _plan(email_app=None))
    before = _snapshot(state)
    state = run_turn(build_graph(RateLimitedLLM()), state, "notify the team")
    assert state.messages[-1].content.startswith("The AI service has reached its usage limit")
    assert "?" not in state.messages[-1].content.split("Your answers so far are kept.")[1]
    assert state.question is None and not state.messages[-1].options
    assert _snapshot(state) == before


def _with_footer(value=None):
    plan = _plan(filled=True)
    plan.steps[2].params.append(_p("footer", "long_text", value=value, evidence=value))
    return plan


def test_workflow_is_kept_across_turns_and_the_old_version_saved():
    state = _generated()
    first = state.workflow
    # Turn 1: the edit needs one more answer; the generated workflow stays.
    state = _run(state, "add a footer to the email", _with_footer())
    assert state.workflow == first and state.target == "send_email.footer" and state.mode == "collecting"
    # Turn 2: the answer completes the edit; the workflow is regenerated and the old version kept.
    state = _run(state, "Sent by the support team", _with_footer("Sent by the support team"))
    assert state.workflow.nodes[2].parameters["footer"] == "Sent by the support team"
    assert state.previous_workflows == [first]
    assert state.messages[-1].content.startswith("Updated")


def test_unchanged_turn_does_not_regenerate():
    state = _generated()
    first = state.workflow
    state = _run(state, "thanks", TurnPlan(message_kind="other"))
    assert state.workflow == first and state.previous_workflows == []


def test_new_workflow_saves_the_current_one():
    state = _generated()
    first = state.workflow
    state = _run(state, "When a GitHub issue is opened, create a Jira ticket", TurnPlan(message_kind="new_request"))
    assert "saved under Previous workflows" in state.messages[-1].content
    state = _run(state, "A", TurnPlan(message_kind="build"))
    assert state.workflow is None and state.previous_workflows == [first] and state.steps == []
