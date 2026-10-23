"""Phase 4: conditions with several comparisons joined by "and" / "or", in the draft, the generated workflow, the
runner and the n8n parameters; and stored single-comparison workflows upgraded when read. The LLM is scripted."""

import json
import sqlite3

import pytest
from test_clarification import _run

from app.catalogue import get
from app.pipeline.generation.schema import ConditionGroup, Workflow, WorkflowNode, migrate_condition_parameters
from app.pipeline.planner_schema import NextQuestion, PlannedParam, PlannedStep, TurnPlan
from app.pipeline.validation import condition_text, structure_errors
from app.runtime import run_workflow
from app.state.models import WorkflowState
from app.state.store import SessionStore

INVOICE_OR_VIP = ("When an invoice arrives in Gmail, if the amount is above ₹1,00,000 or the customer tier is VIP, "
                  "notify the manager on Telegram; otherwise save it to OneDrive.")


def _p(name, value=None, kind="text", evidence=None, label=None, choices=()):
    return PlannedParam(name=name, label=label or name.replace("_", " ").capitalize(), kind=kind, value=value,
                        evidence=evidence or value, choices=list(choices))


def _plan(comparisons, combinator=None, next_question=None):
    """Gmail trigger → one If with `comparisons` [(field, operator, operator words, value, value words), …] →
    yes / no. Evidence is the user's own words, as the planner is told to give it."""
    params = []
    for index, (field, operator, said, value, evidence) in enumerate(comparisons, 1):
        suffix = "" if index == 1 else f"_{index}"
        params += [_p(f"field{suffix}", field, "field", evidence=field.split(".")[-1], label=f"{field} field"),
                   _p(f"operator{suffix}", operator, "operator", evidence=said),
                   _p(f"value{suffix}", value, "number" if value and value.isdigit() else "text", evidence=evidence)]
    if combinator:
        params.append(_p("combinator", combinator, "choice", evidence=combinator, choices=("and", "or")))
    return TurnPlan(message_kind="build", next_question=next_question, workflow_name="Route invoices", steps=[
        PlannedStep(id="trigger", kind="trigger", app="Gmail", app_evidence="Gmail", operation="New invoice email"),
        PlannedStep(id="check", kind="condition", app="If", operation="Check invoice", params=params),
        PlannedStep(id="notify", kind="action", app="Telegram", app_evidence="Telegram", operation="Send alert",
                    branch="true"),
        PlannedStep(id="save", kind="action", app="OneDrive", app_evidence="OneDrive", operation="Upload file",
                    after=["check"], branch="false"),
    ])


AMOUNT = ("invoice.amount", "greater than", "above", "100000", "₹1,00,000")
TIER = ("customer.tier", "equals", "is", "VIP", "VIP")


# -- the draft ----------------------------------------------------------------------------------------------
def test_two_comparisons_with_or_are_kept_in_one_condition():
    state = _run(WorkflowState(), INVOICE_OR_VIP, _plan([AMOUNT, TIER], combinator="or"))
    check = state.step("check")
    assert {p.name: p.value for p in check.params} == {
        "field": "invoice.amount", "operator": "greater_than", "value": "100000",
        "field_2": "customer.tier", "operator_2": "equals", "value_2": "VIP", "combinator": "or"}
    assert check.title == "invoice.amount > 100000 or customer.tier = VIP?"
    assert condition_text(check).startswith("invoice.amount is greater than 100000 or customer.tier is ")
    node = state.workflow.nodes[1]
    assert node.parameters["conditions"] == [
        {"field": "invoice.amount", "operator": "greater_than", "value": "100000"},
        {"field": "customer.tier", "operator": "equals", "value": "VIP"}]
    assert node.parameters["combinator"] == "or"
    assert ConditionGroup.from_parameters(node.parameters).combinator == "or"


def test_a_missing_combinator_is_asked_and_the_answer_completes_the_workflow():
    state = _run(WorkflowState(), INVOICE_OR_VIP.replace(" or the", ", the"), _plan([AMOUNT, TIER]))
    assert state.workflow is None and state.target == "check.combinator"
    assert state.question.text == "Should every one of these conditions be true (and), or is one of them enough (or)?"
    assert [o.label for o in state.question.options] == ["and", "or"] and state.question.allow_custom is False
    state = _run(state, "or", _plan([AMOUNT, TIER]))
    assert state.step("check").combinator == "or" and state.workflow is not None


def test_a_single_comparison_has_no_combinator():
    state = _run(WorkflowState(), INVOICE_OR_VIP.replace(" or the customer tier is VIP", ""), _plan([AMOUNT]))
    names = [p.name for p in state.step("check").params]
    assert names == ["field", "operator", "value"]
    assert state.workflow.nodes[1].parameters["conditions"] == [
        {"field": "invoice.amount", "operator": "greater_than", "value": "100000"}]


def test_at_most_three_comparisons_and_gaps_are_renumbered():
    extra = [("invoice.currency", "equals", "is", "INR", "INR"), ("invoice.status", "equals", "is", "paid", "paid")]
    state = _run(WorkflowState(), INVOICE_OR_VIP + " Currency INR and status paid.",
                 _plan([AMOUNT, TIER, *extra], combinator="and"))
    assert state.step("check").comparison_indexes() == [1, 2, 3]
    plan = _plan([AMOUNT, TIER], combinator="or")
    for param in plan.steps[1].params:  # the planner skipped a number: field_3 instead of field_2
        param.name = param.name.replace("_2", "_3")
    state = _run(WorkflowState(), INVOICE_OR_VIP, plan)
    assert state.step("check").param("field_2").value == "customer.tier"


def test_each_comparison_is_validated():
    state = _run(WorkflowState(), INVOICE_OR_VIP, _plan([AMOUNT, ("customer.tier", "greater than", "above", "VIP", "VIP")], "or"))
    assert any('"VIP", which is not a number' in e for e in structure_errors(state))
    assert state.workflow is None


# -- the runner ---------------------------------------------------------------------------------------------
def _workflow(conditions, combinator):
    nodes = [WorkflowNode(id="hook", type="webhook_trigger", name="Webhook", parameters={"app": "Webhook"}),
             WorkflowNode(id="check", type="condition", name="Check", parameters={
                 "app": "If", "conditions": conditions, "combinator": combinator}),
             WorkflowNode(id="big", type="http_request_post", name="HTTP", parameters={
                 "app": "HTTP Request", "operation": "POST", "url": "https://example.com/big"})]
    return Workflow.model_validate({"metadata": {"name": "t", "trigger_summary": "t", "description": "",
                                                 "created_at": "2026-10-24T00:00:00+00:00"},
                                    "nodes": [n.model_dump() for n in nodes],
                                    "edges": [{"from": "hook", "to": "check", "branch": None},
                                              {"from": "check", "to": "big", "branch": "true"}]})


class OkHTTP:
    def __call__(self, method, url, **kwargs):
        return type("R", (), {"status_code": 200, "text": "", "json": lambda self: {"ok": True}})()


AMOUNT_OVER_500 = {"field": "amount", "operator": "greater_than", "value": "500"}
IS_VIP = {"field": "tier", "operator": "equals", "value": "vip"}


@pytest.mark.parametrize("combinator, data, expected", [
    ("and", {"amount": 900, "tier": "vip"}, True),
    ("and", {"amount": 900, "tier": "basic"}, False),
    ("or", {"amount": 100, "tier": "vip"}, True),
    ("or", {"amount": 100, "tier": "basic"}, False),
])
def test_comparisons_combine_with_and_or(combinator, data, expected):
    results = run_workflow(_workflow([AMOUNT_OVER_500, IS_VIP], combinator), data, send=OkHTTP())
    output = results[1].output
    assert output["result"] is expected and output["combinator"] == combinator
    assert results[2].status == ("ok" if expected else "skipped")


def test_evaluation_stops_once_the_result_is_known():
    # "or": the first comparison holds, so the second (on a field that is missing) is never evaluated.
    results = run_workflow(_workflow([AMOUNT_OVER_500, IS_VIP], "or"), {"amount": 900}, send=OkHTTP())
    assert results[1].status == "ok" and len(results[1].output["conditions"]) == 1
    # "and": the first comparison fails, so the missing field is not reached either.
    results = run_workflow(_workflow([AMOUNT_OVER_500, IS_VIP], "and"), {"amount": 10}, send=OkHTTP())
    assert results[1].status == "ok" and results[1].output["result"] is False
    # Reached, a missing field still fails the step.
    results = run_workflow(_workflow([AMOUNT_OVER_500, IS_VIP], "and"), {"amount": 900}, send=OkHTTP())
    assert results[1].status == "failed" and 'no "tier"' in results[1].error


# -- migration ----------------------------------------------------------------------------------------------
def test_an_old_single_comparison_node_becomes_a_list_of_one():
    old = {"app": "If", "operation": "Check", "field": "amount", "operator": "greater_than", "value": "5"}
    assert migrate_condition_parameters(old) == {
        "app": "If", "operation": "Check", "conditions": [{"field": "amount", "operator": "greater_than", "value": "5"}],
        "combinator": "and"}
    new = migrate_condition_parameters(old)
    assert migrate_condition_parameters(new) == new  # already migrated: unchanged
    node = WorkflowNode(id="c", type="condition", name="c", parameters=old)
    assert "field" not in node.parameters and node.parameters["combinator"] == "and"
    other = WorkflowNode(id="s", type="slack_send_message", name="s", parameters={"field": "x"})
    assert other.parameters == {"field": "x"}  # only condition nodes are touched


def test_a_stored_session_with_an_old_workflow_is_upgraded_when_read_and_still_runs(tmp_path):
    state = _run(WorkflowState(), INVOICE_OR_VIP.replace(" or the customer tier is VIP", ""), _plan([AMOUNT]))
    data = json.loads(state.model_dump_json())
    condition = data["workflow"]["nodes"][1]["parameters"]
    group = condition.pop("conditions")[0]
    condition.pop("combinator")
    condition.update(group)  # written the way sessions were saved before AND/OR
    store = SessionStore(tmp_path / "sessions.db")
    store.save("old", WorkflowState())
    with sqlite3.connect(tmp_path / "sessions.db") as db:
        db.execute("UPDATE sessions SET state = ? WHERE id = 'old'", (json.dumps(data),))
    loaded = store.get("old")
    assert loaded.workflow.nodes[1].parameters["conditions"][0]["field"] == "invoice.amount"
    assert loaded.workflow == state.workflow


# -- n8n ----------------------------------------------------------------------------------------------------
def test_n8n_if_parameters_hold_every_comparison_and_the_combinator():
    params = get("if.condition").n8n.build_params({
        "conditions": [{"field": "={{ $json.amount }}", "operator": "greater_than", "value": "500"},
                       {"field": "={{ $json.tier }}", "operator": "equals", "value": "vip"}],
        "combinator": "or"})
    conditions = params["conditions"]
    assert conditions["combinator"] == "or" and len(conditions["conditions"]) == 2
    assert [c["operator"] for c in conditions["conditions"]] == [
        {"type": "number", "operation": "gt"}, {"type": "string", "operation": "equals"}]


def test_the_planner_is_told_how_to_write_several_comparisons():
    from app.core.llm.client import load_prompt

    assert "field_2, operator_2, value_2" in load_prompt("planner")
    question = NextQuestion(target="check.combinator", question="All or any?")
    assert question.target.endswith("combinator")
