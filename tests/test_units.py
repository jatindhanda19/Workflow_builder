"""Unit tests: normalisation, validators, planner/selector ordering, generator schema validity, diagram builder."""

import pytest
from jsonschema import Draft202012Validator

from app.pipeline.diagram.builder import build_diagram, category_for
from app.pipeline.diagram.mermaid import to_mermaid
from app.pipeline.extraction.normalize import clean_name, parse_field_list, parse_mapping, parse_time, parse_timezone
from app.pipeline.generation.checks import validate_output
from app.pipeline.generation.schema import WORKFLOW_JSON_SCHEMA, Edge, Workflow, WorkflowMetadata, WorkflowNode
from app.pipeline.planning import build_plan
from app.registry import FIELDS, NODE_TYPES
from app.pipeline.questions.selector import select_target
from app.state.models import FieldEntry, WorkflowState
from app.pipeline.validation.consistency import to_template
from app.pipeline.validation.fields import validate
from tests.conftest import Conversation
from tests.test_conversations import run_invoice_until_content, run_reference


# --------------------------------------------------------------------------- extractor normalisation
@pytest.mark.parametrize("key,raw,expected", [
    ("sheet_trigger.file_name", "its is work_update", "work_update"),
    ("sheet_trigger.tab_name", "Watch the work_update tab.", "work_update"),
    ("sheet_trigger.watched_column", "the Status column", "Status"),
    ("email_trigger.label", "Finance  label", "Finance"),
    ("file_trigger.folder", "the   Invoices folder", "Invoices"),
])
def test_names_are_normalised(key: str, raw: str, expected: str) -> None:
    assert clean_name(FIELDS[key], raw) == expected


def test_field_lists_are_structured() -> None:
    assert parse_field_list("it include the details about changes") == ["changed_cell", "old_value", "new_value", "changed_by"]
    assert parse_field_list("the new status, the task name and who updated it") == ["new_value", "task_name", "changed_by"]


def test_free_text_keeps_wording_but_collapses_spaces() -> None:
    assert validate(FIELDS["email.subject"], "  Today's   work update.  ").value == "Today's work update."


@pytest.mark.parametrize("raw,expected", [("6 pm", "18:00"), ("6:30PM", "18:30"), ("18:00", "18:00"), ("9 am IST", "09:00")])
def test_times(raw: str, expected: str) -> None:
    assert parse_time(raw) == (expected, [])


def test_unclear_times_offer_readings() -> None:
    assert parse_time("evening") == (None, ["5:00 PM", "6:00 PM", "7:00 PM"])
    assert parse_time("6") == (None, ["6:00 AM", "6:00 PM"])


@pytest.mark.parametrize("raw,expected", [("IST", "Asia/Kolkata"), ("UTC+5:30", "UTC+05:30"), ("London", "Europe/London")])
def test_timezones(raw: str, expected: str) -> None:
    assert parse_timezone(raw) == expected


def test_mapping_reads_pairs_and_single_answers() -> None:
    assert parse_mapping("client_name from the Name column and amount from Amount", ["client_name", "amount"]) == {
        "client_name": "Name", "amount": "Amount"}
    assert parse_mapping("the Amount column", ["amount"]) == {"amount": "Amount"}


# --------------------------------------------------------------------------- validators
def test_bare_name_is_not_an_email() -> None:
    verdict = validate(FIELDS["email.recipients"], "Jatin")
    assert not verdict.ok and verdict.reason == '"Jatin" is a name, not an email address'
    assert verdict.question == "What's Jatin's email address?"


def test_yes_to_either_or_reasks_with_options() -> None:
    verdict = validate(FIELDS["sheet_trigger.condition_mode"], "yes")
    assert not verdict.ok and "any change or only a specific value" in (verdict.question or "")


def test_vague_values_are_rejected() -> None:
    assert not validate(FIELDS["email.recipients"], "the team").ok
    assert not validate(FIELDS["schedule_trigger.timezone"], "my timezone").ok
    assert not validate(FIELDS["sheet_trigger.file_name"], "I want you to watch the spreadsheet my whole team uses").ok


def test_mapping_needs_every_placeholder() -> None:
    context = {"template.body": "Hi {name}, you owe {amount}"}
    assert not validate(FIELDS["template.variables"], "name from Name", context).ok
    assert validate(FIELDS["template.variables"], "name from Name and amount from Amount", context).value == [
        "name=Name", "amount=Amount"]


def test_fixed_text_placeholders_become_template_variables() -> None:
    assert to_template("Please pay the amount by the due date", ["due date", "amount"]) == "Please pay the {amount} by the {due_date}"


# --------------------------------------------------------------------------- planner / selector
def test_condition_questions_apply_only_to_column_value_changed() -> None:
    base = {"goal.action": "notify", "trigger.kind": "sheet_event", "action.channel": "email"}
    cell = build_plan({**base, "sheet_trigger.event": "cell_updated"}, None)
    column = build_plan({**base, "sheet_trigger.event": "column_value_changed"}, None)
    assert "sheet_trigger.condition_mode" in {f.key for f in cell.not_applicable}
    assert "sheet_trigger.condition_mode" in {f.key for f in column.fields}
    keys = [f.key for f in column.fields]
    assert keys.index("sheet_trigger.watched_column") < keys.index("sheet_trigger.condition_mode")


def test_outbound_goal_asks_trigger_first_then_sources() -> None:
    plan = build_plan({"goal.action": "send_document"}, "dynamic")
    keys = [f.key for f in plan.fields]
    assert keys[:2] == ["goal.action", "trigger.kind"]
    assert keys.index("document.source_kind") < keys.index("recipients.source") < keys.index("action.channel")


def test_selector_skips_filled_fields_and_respects_depends_on() -> None:
    state = WorkflowState()
    for key, value in {"goal.action": "notify", "trigger.kind": "sheet_event", "sheet_trigger.platform": "excel"}.items():
        state.fields[key] = FieldEntry(key=key, status="filled", value=value)
    plan = build_plan(state.values(), None)
    assert select_target(state, plan).key == "sheet_trigger.file_name"


def test_batched_delivery_requires_frequency_time_timezone() -> None:
    values = {"goal.action": "notify", "trigger.kind": "sheet_event", "action.channel": "email",
              "email.content_mode": "summary", "delivery.mode": "batched_digest"}
    keys = {f.key for f in build_plan(values, None).fields}
    assert {"delivery.frequency", "delivery.time", "delivery.timezone"} <= keys


def test_registry_is_consistent() -> None:
    keys = [f.key for n in NODE_TYPES for f in n.fields]
    assert len(keys) == len(set(keys))
    for node in NODE_TYPES:
        for fdef in node.fields:
            assert all(dep in FIELDS for dep in fdef.depends_on), fdef.key
            assert fdef.kind != "choice" or fdef.choices, fdef.key


# --------------------------------------------------------------------------- generator
def test_generated_workflows_match_the_json_schema(convo: Conversation) -> None:
    run_reference(convo)
    data = convo.state.workflow.model_dump(by_alias=True)
    assert not list(Draft202012Validator(WORKFLOW_JSON_SCHEMA).iter_errors(data))
    assert data["edges"][0]["branch"] is None


def test_output_checks_reject_a_condition_without_false_branch() -> None:
    workflow = Workflow(
        metadata=WorkflowMetadata(name="x", trigger_summary="x", description="", created_at="2026-01-01T00:00:00+00:00"),
        nodes=[WorkflowNode(id="trigger_1", type="manual_trigger", name="T"),
               WorkflowNode(id="condition_1", type="condition", name="C"),
               WorkflowNode(id="email_1", type="send_email", name="E")],
        edges=[Edge(source="trigger_1", target="condition_1"),
               Edge(source="condition_1", target="email_1", branch="true")],
    )
    assert any("true and one false" in e for e in validate_output(workflow, {}))


# --------------------------------------------------------------------------- diagram
def test_diagram_from_reference_json(convo: Conversation) -> None:
    run_reference(convo)
    diagram = build_diagram(convo.state.workflow.model_dump(by_alias=True))
    categories = {n.id: n.category for n in diagram.nodes}
    assert categories["trigger_1"] == "trigger" and categories["slack_1"] == "action"
    assert categories["extract_1"] == "data" and categories["condition_1"] == "logic"
    labels = {(e.source, e.target): e.label for e in diagram.edges}
    assert labels[("condition_1", "slack_1")] == "Yes" and labels[("condition_1", "end_1")] == "No"
    assert next(n for n in diagram.nodes if n.id == "trigger_1").subtitle == "Label: Finance"
    text = to_mermaid(diagram)
    assert text.startswith("flowchart LR") and "-->|Yes|" in text and "-->|No|" in text
    assert 'click condition_1 showNode' in text


def test_diagram_draws_loops(convo: Conversation) -> None:
    run_invoice_until_content(convo)
    for answer in ("use a template", "Hi {client_name}", "the Name column", "Your invoice"):
        convo.say(answer)
    diagram = build_diagram(convo.state.workflow.model_dump(by_alias=True))
    back = [e for e in diagram.edges if e.dashed]
    assert back and back[0].target == "loop_1" and back[0].label == "next item"
    assert 'subgraph loop_scope["For each client"]' in to_mermaid(diagram)


def test_live_preview_marks_pending_nodes(convo: Conversation) -> None:
    from app.pipeline.generation.builder import preview_workflow

    convo.say("auto send invoice to client", intent={"goal_action": "send_document", "entities": ["invoice", "client"],
                                                     "recipient_cardinality": "dynamic"})
    built = preview_workflow(convo.state)
    diagram = build_diagram(built.workflow.model_dump(by_alias=True), built.pending)
    assert diagram.nodes[0].pending and diagram.nodes[0].category == "trigger"
    assert "classDef pending" in to_mermaid(diagram) and ":::pending" in to_mermaid(diagram)


@pytest.mark.parametrize("workflow_type,category", [
    ("gmail_trigger", "trigger"), ("excel_trigger", "trigger"), ("aggregate_batch", "logic"),
    ("read_sheet_rows", "data"), ("sheet_append_row", "action"), ("end", "logic"),
])
def test_categories_come_from_the_registry(workflow_type: str, category: str) -> None:
    assert category_for(workflow_type) == category
