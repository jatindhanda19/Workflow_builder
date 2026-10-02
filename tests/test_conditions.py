"""Conditions: a field is a data reference, never a number; "otherwise" is a real false branch.

The LLM is scripted, so these test the code path only: merge, validation, questions and generation.
"""

import pytest
from test_clarification import _run

from app.pipeline.diagram.mermaid import to_mermaid
from app.pipeline.graph import _plan_text
from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.state.models import WorkflowState

INVOICE = ("When an invoice arrives in Gmail, if the invoice amount is above ₹1,00,000, notify the manager on Telegram; "
           "otherwise, save it to OneDrive.")
# The same request without naming the data, so the field has to be asked.
INVOICE_NO_FIELD = ("When an invoice arrives in Gmail, if it is above ₹1,00,000, notify the manager on Telegram; "
                    "otherwise, save it to OneDrive.")
FIELD_ASK = NextQuestion(target="check_amount.field", question="Which part of the invoice holds the amount?")
FIELD_REASON = ("I need the field that contains the invoice amount, such as amount, total, or invoice.amount. "
                "10000 looks like a value, not a field name")


def _p(name, value=None, evidence=None, kind="text", label=None):
    return PlannedParam(name=name, label=label or name.replace("_", " ").capitalize(), kind=kind, value=value,
                        evidence=evidence or value)


def _condition_plan(subject, condition_params, true_step, false_step, trigger=("Gmail", "Gmail", "New email"),
                    next_question=None, false_branch="false"):
    """trigger → condition → true step / false step. Steps are (id, app, operation)."""
    app, evidence, operation = trigger
    steps = [
        PlannedStep(id="trigger", kind="trigger", app=app, app_evidence=evidence, operation=operation),
        PlannedStep(id=f"check_{subject}", kind="condition", app="If", operation=f"Check {subject}",
                    params=condition_params),
        PlannedStep(id=true_step[0], kind="action", app=true_step[1], app_evidence=true_step[1],
                    operation=true_step[2], branch="true"),
    ]
    if false_step:
        steps.append(PlannedStep(id=false_step[0], kind="action", app=false_step[1], app_evidence=false_step[1],
                                 operation=false_step[2], branch=false_branch))
    return TurnPlan(message_kind="build", workflow_name=f"Route by {subject}", steps=steps, next_question=next_question)


def _invoice_plan(field=None, field_name="field", value="100000", value_evidence="₹1,00,000", **kwargs):
    params = [
        _p(field_name, field, kind="field" if field_name == "field" else "number", label="Invoice amount field"),
        _p("operator", "greater than", "above", kind="choice"),
        _p("value", value, value_evidence, kind="number"),
    ]
    return _condition_plan("amount", params, ("notify_manager", "Telegram", "Send message"),
                           ("save_invoice", "OneDrive", "Upload file"),
                           trigger=("Gmail", "Gmail", "New invoice email"), **kwargs)


def _edges(state):
    return {(e.source, e.target, e.branch) for e in state.workflow.edges}


def _condition(state, step_id="check_amount"):
    step = state.step(step_id)
    return {p.name: p.value for p in step.params}


# a. "If invoice amount is above 100000, notify manager; otherwise save it."
def test_a_otherwise_builds_two_branches_and_keeps_the_condition():
    state = _run(WorkflowState(), INVOICE, _invoice_plan(field="invoice.amount"))
    assert state.workflow is not None, state.messages[-1].content
    assert _condition(state) == {"field": "invoice.amount", "operator": "greater_than", "value": "100000"}
    assert _edges(state) == {
        ("trigger", "check_amount", None),
        ("check_amount", "notify_manager", "true"),
        ("check_amount", "save_invoice", "false"),
    }
    # Saving is the false branch only: it is not chained after the notification, and nothing ends early.
    assert not any(n.type == "end" for n in state.workflow.nodes)


# b. "#amount" typed as the field is a field reference, normalised; never a number.
def test_b_hash_field_is_normalised_to_a_reference():
    state = _run(WorkflowState(), INVOICE_NO_FIELD, _invoice_plan(next_question=FIELD_ASK))
    assert state.target == "check_amount.field" and state.workflow is None
    state = _run(state, "#amount", _invoice_plan())  # the LLM did not fill it: code reads the typed answer
    assert _condition(state) == {"field": "invoice.amount", "operator": "greater_than", "value": "100000"}
    assert state.workflow is not None


# c. "10000" typed where the field is asked is rejected with an explanation, and changes nothing else.
@pytest.mark.parametrize("plan", [
    # The reported bug: the LLM put the answer in a parameter named after the data ("amount").
    _invoice_plan(field="10000", field_name="amount", value_evidence="₹1,00,000"),
    # The LLM read the answer as a new threshold.
    _invoice_plan(value="10000", value_evidence="10000"),
    # The LLM ignored the answer.
    _invoice_plan(),
], ids=["as-field-named-amount", "as-new-threshold", "ignored"])
def test_c_number_typed_as_field_asks_for_clarification(plan):
    state = _run(WorkflowState(), INVOICE_NO_FIELD, _invoice_plan(next_question=FIELD_ASK))
    state = _run(state, "10000", plan)
    assert _condition(state) == {"field": None, "operator": "greater_than", "value": "100000"}
    assert state.workflow is None and state.target == "check_amount.field"
    assert FIELD_REASON in state.question.text
    assert not any("10000" == str(p.value) for s in state.steps for p in s.params)


def test_c_number_in_the_request_is_the_value_never_the_field():
    request = ("When an invoice arrives in Gmail, if the invoice amount is above 10000, notify the manager on Telegram; "
               "otherwise, save it to OneDrive.")
    # The LLM put the threshold in the field: it is rejected, and the field is read from the user's sentence.
    state = _run(WorkflowState(), request, _invoice_plan(field="10000", value="10000", value_evidence="10000"))
    assert _condition(state) == {"field": "invoice.amount", "operator": "greater_than", "value": "10000"}
    assert state.workflow is not None


# Regression: "if the order total is above ₹50,000" once yielded only the value; the comparison was then asked,
# answered "less than", and the workflow was generated with the opposite condition.
ORDERS = ("When a new order is created in Square, if the order total is above ₹50,000, notify the sales manager on "
          "Slack; otherwise, add the order to MySQL.")


def _orders_plan(operator=None, operator_evidence=None, **kwargs):
    params = [_p("order_total", label="Order total field", kind="number"),
              _p("comparison_operator", operator, operator_evidence, kind="choice"),
              _p("value", "50000", "₹50,000", kind="number")]
    return _condition_plan("order_total", params, ("notify_sales_manager", "Slack", "Send message"),
                           ("add_order_to_db", "MySQL", "Insert row"), trigger=("Square", "Square", "New order"),
                           **kwargs)


def test_condition_stated_in_the_request_is_never_asked():
    state = _run(WorkflowState(), ORDERS, _orders_plan())
    assert _condition(state, "check_order_total") == {
        "field": "order.total", "operator": "greater_than", "value": "50000"}
    assert state.workflow is not None
    assert state.workflow.nodes[1].name == "order.total > 50000?"
    assert state.workflow.metadata.description == (
        "Square: New order → order.total > 50000? → yes: Slack: Send message / no: MySQL: Insert row")


def test_misread_comparison_is_corrected_by_the_users_words():
    state = _run(WorkflowState(), ORDERS, _orders_plan("less than", "above"))
    assert _condition(state, "check_order_total")["operator"] == "greater_than"


def test_explicit_change_of_comparison_is_kept():
    state = _run(WorkflowState(), ORDERS, _orders_plan())
    state = _run(state, "change the comparison to less than", _orders_plan("less than", "less than"))
    assert _condition(state, "check_order_total")["operator"] == "less_than"
    assert state.workflow.nodes[1].name == "order.total < 50000?"


# d. TRUE and FALSE branches in the generated workflow and diagram.
def test_d_true_and_false_branches_in_workflow_and_diagram():
    state = _run(WorkflowState(), INVOICE, _invoice_plan(field="invoice.amount"))
    diagram = to_mermaid(state.workflow.model_dump(by_alias=True))
    assert "n_check_amount -->|Yes| n_notify_manager" in diagram
    assert "n_check_amount -->|No| n_save_invoice" in diagram
    assert "n_notify_manager --> n_save_invoice" not in diagram


def test_d_step_after_both_branches_joins_them():
    plan = _invoice_plan(field="invoice.amount")
    plan.steps.append(PlannedStep(id="log_row", kind="action", app="Google Sheets", app_evidence="Google Sheets",
                                  operation="Append row", after=["notify_manager", "save_invoice"]))
    state = _run(WorkflowState(), INVOICE + " In both cases log it in Google Sheets.", plan)
    assert {("notify_manager", "log_row", None), ("save_invoice", "log_row", None)} <= _edges(state)


def test_d_without_otherwise_false_ends():
    plan = _invoice_plan(field="invoice.amount")
    plan.steps.pop()  # no false step
    state = _run(WorkflowState(), "When an invoice arrives in Gmail, if the invoice amount is above ₹1,00,000, "
                                  "notify the manager on Telegram.", plan)
    assert ("check_amount", "end_1", "false") in _edges(state)


# e. FALSE does not terminate when "otherwise" names an action.
def test_e_otherwise_action_missing_from_plan_is_asked_not_ended():
    plan = _invoice_plan(field="invoice.amount")
    plan.steps.pop()  # the LLM dropped the "otherwise" action
    state = _run(WorkflowState(), INVOICE, plan)
    assert state.workflow is None and state.target == "clarify"
    assert "otherwise" in state.question.text and "invoice.amount is greater than 100000" in state.question.text


def test_e_otherwise_action_chained_after_true_is_not_accepted():
    # The reported bug: Gmail → check → TRUE → Telegram → OneDrive, FALSE → End.
    plan = _invoice_plan(field="invoice.amount")
    for step in plan.steps:
        step.branch = None
    state = _run(WorkflowState(), INVOICE, plan)
    assert state.workflow is None and state.target == "clarify"


def test_e_otherwise_ignore_may_end():
    plan = _invoice_plan(field="invoice.amount")
    plan.steps.pop()
    state = _run(WorkflowState(), "When an invoice arrives in Gmail, if the invoice amount is above ₹1,00,000, "
                                  "notify the manager on Telegram; otherwise ignore it.", plan)
    assert state.workflow is not None and ("check_amount", "end_1", "false") in _edges(state)


# Not specific to invoices.
@pytest.mark.parametrize("request_text, trigger_app, subject, field, operator_words, value, value_evidence, expected, true_step, false_step", [
    ("When a new order arrives in Shopify, if the order total > 5000, notify sales on Slack; otherwise save the order "
     "to Airtable.", "Shopify", "total", ("order.total", "order total"), (">", ">"), "5000", "5000",
     ("order.total", "greater_than", "5000"), ("notify_sales", "Slack", "Send message"),
     ("save_order", "Airtable", "Create record")),
    ("When a lead comes in from Typeform, if the lead score < 50, send an email with Gmail; otherwise create a CRM "
     "record in HubSpot.", "Typeform", "score", ("#score", "#score"), ("less than", "<"), "50", "50",
     ("lead.score", "less_than", "50"), ("send_email", "Gmail", "Send email"),
     ("create_contact", "HubSpot", "Create contact")),
    ("When a file is added to Google Drive, if file size >= 10 MB, notify the admin on Slack; otherwise continue "
     "processing with HTTP Request.", "Google Drive", "size", ("file.size", "file size"), (">=", ">="), "10", "10 MB",
     ("file.size", "greater_than_or_equal", "10"), ("notify_admin", "Slack", "Send message"),
     ("process_file", "HTTP Request", "POST request")),
])
def test_generic_conditions(request_text, trigger_app, subject, field, operator_words, value, value_evidence, expected,
                            true_step, false_step):
    label = {"total": "Order total field", "score": "Lead score field", "size": "File size field"}[subject]
    params = [_p("field", field[0], field[1], kind="field", label=label),
              _p("operator", operator_words[0], operator_words[1], kind="operator"),
              _p("value", value, value_evidence, kind="number")]
    plan = _condition_plan(subject, params, true_step, false_step, trigger=(trigger_app, trigger_app, "New item"))
    state = _run(WorkflowState(), request_text, plan)
    assert state.workflow is not None, state.messages[-1].content
    cond = f"check_{subject}"
    assert tuple(_condition(state, cond).values()) == expected
    assert {(cond, true_step[0], "true"), (cond, false_step[0], "false")} <= _edges(state)
    assert (true_step[0], false_step[0], None) not in _edges(state)


# A custom app typed when the app is asked is taken, even if the LLM calls the reply unclear.
@pytest.mark.parametrize("typed, expected", [("My Restaurent", "My Restaurent"), ("app is MY Restaurent", "MY Restaurent"),
                                             ("use Zoho", "Zoho")])
def test_typed_custom_app_answers_the_app_question(typed, expected):
    plan = _orders_plan(next_question=NextQuestion(target="trigger.app", question="Which app has the new orders?"))
    plan.steps[0].app = None
    state = _run(WorkflowState(), ORDERS.replace(" in Square", ""), plan)
    assert state.target == "trigger.app"
    unclear = TurnPlan(message_kind="unclear", next_question=NextQuestion(
        target="clarify", question="Which app should I monitor for new orders from your restaurant?"))
    state = _run(state, typed, unclear)
    assert state.step("trigger").app == expected and state.target != "clarify"


@pytest.mark.parametrize("typed", ["not sure", "which one is best?", "I use Shopify and Square"])
def test_non_answers_are_not_taken_as_an_app(typed):
    plan = _orders_plan(next_question=NextQuestion(target="trigger.app", question="Which app has the new orders?"))
    plan.steps[0].app = None
    state = _run(WorkflowState(), ORDERS.replace(" in Square", ""), plan)
    state = _run(state, typed, TurnPlan(message_kind="unclear"))
    assert state.step("trigger").app is None and state.target == "clarify"


# Steps name the earlier steps they follow ("after"); edges are built from that.
NESTED = ("When an invoice arrives in Gmail, if the invoice amount is above ₹1,00,000, then if the customer score is "
          "above 80 notify the CEO on Slack, otherwise notify the manager on Telegram; otherwise save it to OneDrive.")


def _action(step_id, app, after=(), branch=None):
    return PlannedStep(id=step_id, kind="action", app=app, app_evidence=app, operation="Send message",
                       after=list(after), branch=branch)


def _nested_plan(*actions):
    def condition(step_id, label, **link):
        return PlannedStep(id=step_id, kind="condition", app="If", operation="Check", params=[
            _p("field", kind="field", label=label), _p("operator", kind="operator"), _p("value", kind="number")], **link)

    return TurnPlan(message_kind="build", steps=[
        PlannedStep(id="trigger", kind="trigger", app="Gmail", app_evidence="Gmail", operation="New invoice email"),
        condition("check_amount", "Invoice amount field"),
        condition("check_score", "Customer score field", after=["check_amount"], branch="true"),
        *actions,
    ])


NESTED_ACTIONS = (_action("notify_ceo", "Slack", ["check_score"], "true"),
                  _action("notify_manager", "Telegram", ["check_score"], "false"),
                  _action("save_invoice", "OneDrive", ["check_amount"], "false"))


def test_condition_inside_a_condition_outcome():
    state = _run(WorkflowState(), NESTED, _nested_plan(*NESTED_ACTIONS))
    assert state.workflow is not None, state.messages[-1].content
    assert _edges(state) == {
        ("trigger", "check_amount", None),
        ("check_amount", "check_score", "true"),
        ("check_amount", "save_invoice", "false"),
        ("check_score", "notify_ceo", "true"),
        ("check_score", "notify_manager", "false"),
    }
    assert _condition(state, "check_score") == {"field": "customer.score", "operator": "greater_than", "value": "80"}
    assert state.workflow.metadata.description == (
        "Gmail: New invoice email → invoice.amount > 100000? → yes: customer.score > 80? → yes: Slack: Send message "
        "/ no: Telegram: Send message / no: OneDrive: Send message")


def test_plan_text_names_the_step_a_branch_follows():
    state = _run(WorkflowState(), NESTED, _nested_plan(*NESTED_ACTIONS))
    plan = _plan_text(state)
    assert "**Otherwise:** OneDrive: Send message _(after invoice.amount > 100000?)_" in plan
    assert "**If yes:** Slack: Send message\n" in plan  # right after its condition: nothing to add


@pytest.mark.parametrize("bad, expected", [
    (_action("save_invoice", "OneDrive", ["archive_step"], "false"), 'after "archive_step", which is not a step'),
    (_action("save_invoice", "OneDrive", ["notify_later"], None), "which runs later"),
], ids=["unknown-step", "later-step"])
def test_a_step_following_a_missing_or_later_step_is_asked(bad, expected):
    actions = [*NESTED_ACTIONS[:2], bad, _action("notify_later", "Slack", ["check_amount"], "false")]
    state = _run(WorkflowState(), NESTED, _nested_plan(*actions))
    assert state.workflow is None and state.target == "clarify" and expected in state.question.text


def test_second_step_on_the_same_outcome_runs_after_the_first():
    actions = [*NESTED_ACTIONS, _action("log_it", "Slack", ["check_amount"], "false")]
    state = _run(WorkflowState(), NESTED, _nested_plan(*actions))
    assert {("check_amount", "save_invoice", "false"), ("save_invoice", "log_it", None)} <= _edges(state)


def test_step_on_an_outcome_that_ends_in_a_condition_is_asked():
    # The yes outcome of check_amount goes into check_score: there is no single last step to run after.
    actions = [*NESTED_ACTIONS, _action("log_it", "Slack", ["check_amount"], "true")]
    state = _run(WorkflowState(), NESTED, _nested_plan(*actions))
    assert state.workflow is None and "Should they run one after the other?" in state.question.text


# Replay of a real session: "check whether …" was not read, and both "if it is" steps started the yes outcome.
SHOPIFY = ("When a new order is received through Shopify, check whether the order value is above ₹50,000. If it is, "
           "add the order details to Airtable and send a notification to the finance team in Slack")


def test_check_whether_and_two_actions_on_yes_become_a_chain():
    plan = TurnPlan(message_kind="build", steps=[
        PlannedStep(id="shopify_trigger", kind="trigger", app="Shopify", app_evidence="Shopify", operation="New order"),
        PlannedStep(id="check_order_value", kind="condition", app="If", operation="Check order value", params=[
            _p("field", kind="field", label="Order value field"), _p("operator", "greater than", "above", "operator"),
            _p("value", "50000", "₹50,000", "number")]),
        _action("add_airtable", "Airtable", ["check_order_value"], "true"),
        _action("send_slack", "Slack", ["check_order_value"], "true"),
    ])
    state = _run(WorkflowState(), SHOPIFY, plan)
    assert _condition(state, "check_order_value") == {
        "field": "order.value", "operator": "greater_than", "value": "50000"}
    assert _edges(state) == {
        ("shopify_trigger", "check_order_value", None),
        ("check_order_value", "add_airtable", "true"),
        ("add_airtable", "send_slack", None),
        ("check_order_value", "end_1", "false"),
    }
    assert _plan_text(state).endswith("3. **If yes:** Airtable: Send message\n4. **Then:** Slack: Send message")


@pytest.mark.parametrize("typed, kind", [("thanks", "other"), ("thanks", "unclear"), ("hello", "build")])
def test_small_talk_is_not_taken_as_an_app(typed, kind):
    plan = _orders_plan(next_question=NextQuestion(target="trigger.app", question="Which app has the new orders?"))
    plan.steps[0].app = None
    state = _run(WorkflowState(), ORDERS.replace(" in Square", ""), plan)
    state = _run(state, typed, TurnPlan(message_kind=kind))
    assert state.step("trigger").app is None


# Replay of a real session: two conditions in one request were crossed ("Email field: order.value"), and
# "is missing" could not be expressed.
TWO_CONDITIONS = (
    "When a new order is received through Shopify, check whether the order value is above ₹50,000. If it is, add the "
    "order details to Airtable and send a notification to the finance team in Slack. Otherwise, add the order to a "
    "separate Airtable table for regular orders. If the customer’s email is missing, send an alert to the operations "
    "team instead.")


def _two_conditions_plan():
    def condition(step_id, label, **link):
        return PlannedStep(id=step_id, kind="condition", app="If", operation=f"Check {label.lower()}", params=[
            _p("field", kind="field", label=f"{label} field"), _p("operator", kind="operator"),
            _p("value", kind="text")], **link)

    return TurnPlan(message_kind="build", steps=[
        PlannedStep(id="shopify_trigger", kind="trigger", app="Shopify", app_evidence="Shopify", operation="New order"),
        condition("check_email", "Email"),
        PlannedStep(id="alert_ops", kind="action", operation="Send message", after=["check_email"], branch="true"),
        condition("check_order_value", "Order value", after=["check_email"], branch="false"),
        _action("add_high_value", "Airtable", ["check_order_value"], "true"),
        _action("notify_finance", "Slack", ["add_high_value"]),
        _action("add_regular", "Airtable", ["check_order_value"], "false"),
    ])


def test_two_conditions_are_matched_by_subject_not_position():
    state = _run(WorkflowState(), TWO_CONDITIONS, _two_conditions_plan())
    assert _condition(state, "check_email") == {"field": "customer.email", "operator": "is_empty", "value": None}
    assert _condition(state, "check_order_value") == {
        "field": "order.value", "operator": "greater_than", "value": "50000"}
    assert [state.step(s).title for s in ("check_email", "check_order_value")] == [
        "customer.email is empty?", "order.value > 50000?"]
    assert state.target == "alert_ops.app"  # "is missing" needs no value: the next open item is the alert's app

    state = _run(state, "Slack", _two_conditions_plan())
    assert _edges(state) == {
        ("shopify_trigger", "check_email", None),
        ("check_email", "alert_ops", "true"),
        ("check_email", "check_order_value", "false"),
        ("check_order_value", "add_high_value", "true"),
        ("add_high_value", "notify_finance", None),
        ("check_order_value", "add_regular", "false"),
    }
