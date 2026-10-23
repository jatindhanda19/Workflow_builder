"""Phase 3: gap detection. References must point to earlier steps and to data those steps produce; a gap is
asked as a targeted question and the missing step is never invented. The LLM is scripted."""

from test_clarification import _run

from app.core.turnlog import turn_record
from app.pipeline.gaps import completeness_gaps, references, upstream
from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.state.models import WorkflowState

REQUEST = "When I get a new email in Gmail, post it to Slack #inbox"


def _p(name, value=None, kind="text", evidence=None):
    return PlannedParam(name=name, label=name.capitalize(), kind=kind, value=value, evidence=evidence or value)


def _gmail_to_slack(message, next_question=None, extra_steps=()):
    return TurnPlan(message_kind="build", next_question=next_question, steps=[
        PlannedStep(id="gmail_trigger", kind="trigger", app="Gmail", app_evidence="Gmail", operation="On new email"),
        PlannedStep(id="notify", kind="action", app="Slack", app_evidence="Slack", operation="Send message",
                    params=[_p("channel", "#inbox", "channel"), _p("message", message, "long_text",
                                                                  evidence="post it")]),
        *extra_steps,
    ])


def test_references_are_read_from_text_and_lists():
    assert list(references("From {{gmail_trigger.from}}: {{ gmail_trigger.subject }}")) == [
        ("{{gmail_trigger.from}}", "gmail_trigger", "from"), ("{{ gmail_trigger.subject }}", "gmail_trigger", "subject")]
    assert list(references(["Name={{trigger.customer.name}}", "Total=5"])) == [
        ("{{trigger.customer.name}}", "trigger", "customer.name")]
    assert list(references("{{trigger}}")) == [("{{trigger}}", "trigger", "")]
    assert list(references(None)) == []


def test_valid_references_are_kept_and_the_workflow_is_generated():
    state = _run(WorkflowState(), REQUEST, _gmail_to_slack("New email from {{gmail_trigger.from}}: "
                                                           "{{gmail_trigger.subject}}"))
    assert state.step("notify").param("message").value.startswith("New email from {{gmail_trigger.from}}")
    assert state.workflow is not None and completeness_gaps(state) == []


def test_a_reference_to_a_missing_step_is_asked_never_invented():
    state = _run(WorkflowState(), REQUEST + " with a summary", _gmail_to_slack("Summary: {{summarize.output}}"))
    message = state.step("notify").param("message")
    assert message.value is None
    assert message.note == '{{summarize.output}} uses a step "summarize" that is not part of this workflow'
    assert state.step("summarize") is None and [s.id for s in state.steps] == ["gmail_trigger", "notify"]
    assert state.workflow is None and state.target == "notify.message"
    assert state.question.note == message.note
    # What the step can use instead comes first: the Gmail trigger's catalogue fields.
    assert [o.label for o in state.question.options][:2] == ["{{gmail_trigger.id}}", "{{gmail_trigger.thread_id}}"]
    assert "Got it: Message" not in state.messages[-1].content  # it was not stored, so it is not acknowledged


def test_a_field_the_source_does_not_produce_is_asked():
    state = _run(WorkflowState(), REQUEST, _gmail_to_slack("{{gmail_trigger.body}}"))
    note = state.step("notify").param("message").note
    assert note == ('{{gmail_trigger.body}} asks "Gmail: On new email" for "body", but it gives id, thread_id, from, '
                    'to, subject, snippet')
    assert state.workflow is None and state.target == "notify.message"


def test_dynamic_and_unverified_sources_can_produce_any_field():
    webhook = TurnPlan(message_kind="build", steps=[
        PlannedStep(id="hook", kind="trigger", app="Webhook", app_evidence="webhook", operation="On webhook call"),
        PlannedStep(id="crm", kind="action", app="HubSpot", app_evidence="HubSpot", operation="Create contact",
                    params=[_p("email", "{{hook.customer.email}}", evidence="the customer")]),
        PlannedStep(id="notify", kind="action", app="Slack", app_evidence="Slack", operation="Send message",
                    params=[_p("channel", "#sales", "channel"), _p("message", "New contact {{crm.id}}", "long_text",
                                                                  evidence="post it")]),
    ])
    state = _run(WorkflowState(), "When my webhook is called, create the customer in HubSpot and post it to Slack "
                                  "#sales", webhook)
    assert completeness_gaps(state) == [] and state.workflow is not None


def test_a_step_cannot_use_data_from_a_later_step_or_another_branch():
    plan = TurnPlan(message_kind="build", steps=[
        PlannedStep(id="hook", kind="trigger", app="Webhook", app_evidence="webhook", operation="On webhook call"),
        PlannedStep(id="check", kind="condition", app="If", operation="Check amount", params=[
            _p("field", "{{hook.amount}}", "field", evidence="amount"), _p("operator", "greater than", "operator",
                                                                          evidence="above"),
            _p("value", "100", "number")]),
        PlannedStep(id="big", kind="action", app="HTTP Request", app_evidence="HTTP Request", operation="POST",
                    branch="true", params=[_p("url", "https://example.com/big", "url")]),
        PlannedStep(id="small", kind="action", app="Slack", app_evidence="Slack", operation="Send message",
                    after=["check"], branch="false",
                    params=[_p("channel", "#ops", "channel"), _p("message", "Done: {{big.status}}", "long_text",
                                                                evidence="post it")]),
    ])
    state = _run(WorkflowState(), "When my webhook is called, if the amount is above 100, POST it to "
                                  "https://example.com/big with HTTP Request; otherwise post it to Slack #ops", plan)
    assert [s.id for s in upstream(state, "small")] == ["hook", "check"]
    assert state.step("small").param("message").note == (
        '{{big.status}} uses "HTTP Request: POST", which does not run before this step, so its data is not there')


def test_options_that_reference_missing_data_are_not_offered():
    ask = NextQuestion(target="notify.message", question="What should the Slack message say?",
                       options=["{{gmail_trigger.body}}", "New email: {{gmail_trigger.subject}}", "{{summary.text}}"])
    state = _run(WorkflowState(), REQUEST, _gmail_to_slack(None, next_question=ask))
    assert [o.label for o in state.question.options] == ["New email: {{gmail_trigger.subject}}"]


def test_completeness_gaps_cover_trigger_action_and_branches():
    assert completeness_gaps(WorkflowState()) == []
    no_action = _run(WorkflowState(), "When I get a new email in Gmail", TurnPlan(message_kind="build", steps=[
        PlannedStep(id="gmail_trigger", kind="trigger", app="Gmail", app_evidence="Gmail", operation="On new email")]))
    assert "The workflow has no action yet: what should it do?" in completeness_gaps(no_action)
    otherwise = TurnPlan(message_kind="build", steps=[
        PlannedStep(id="hook", kind="trigger", app="Webhook", app_evidence="webhook", operation="On webhook call"),
        PlannedStep(id="check", kind="condition", app="If", operation="Check amount", params=[
            _p("field", "{{hook.amount}}", "field", evidence="amount"),
            _p("operator", "greater than", "operator", evidence="above"), _p("value", "100", "number")]),
        PlannedStep(id="big", kind="action", app="HTTP Request", app_evidence="HTTP Request", operation="POST",
                    branch="true", params=[_p("url", "https://example.com/big", "url")]),
    ])
    state = _run(WorkflowState(), "When my webhook is called, if the amount is above 100, POST it to "
                                  "https://example.com/big with HTTP Request; otherwise email me", otherwise)
    gaps = completeness_gaps(state)
    assert any("nothing is set up for when" in g for g in gaps) and state.workflow is None


def test_the_turn_log_lists_the_gaps():
    before = WorkflowState()
    after = _run(before, REQUEST, _gmail_to_slack("{{gmail_trigger.body}}"))
    assert turn_record("s1", before, after, latency_ms=1)["gaps"] == []  # cleared and asked, so no gap remains
    assert any(r["field"] == "notify.message" and "gives id" in r["reason"]
               for r in turn_record("s1", before, after, latency_ms=1)["rejected"])
