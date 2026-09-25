"""Builds the workflow JSON from validated state. No LLM: the same state always gives the same workflow.

Nodes come from the plan in stage order; each node's type, name and parameters come
from its registry entry. Only a few nodes need wiring beyond that (condition
branches, loop scope, email inputs). A preview mode builds the partial workflow
shown while fields are still being collected, with undecided nodes marked pending.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.pipeline.generation.checks import validate_output
from app.pipeline.generation.schema import Edge, Workflow, WorkflowMetadata, WorkflowNode
from app.pipeline.planning import Plan, PlannedNode, build_plan
from app.registry import FIELDS, NodeType
from app.registry.display import display_value, fill_template, plural, summary_line, template_values
from app.state.models import FieldValue, WorkflowState
from app.pipeline.validation.readiness import evaluate

PARAM_NAMES = {
    "email.recipients": "to", "email.body_text": "body",
    "save_file.sheet_content": "content", "save_file.email_content": "content",
}
DEDUPE_KEYS = {
    "sheet_trigger": ["file_name", "tab_name", "row_id", "changed_cell", "new_value"],
    "email_trigger": ["message_id"], "file_trigger": ["file_id"], "form_trigger": ["response_id"],
}
CURRENCIES = {"₹": "INR", "rs": "INR", "inr": "INR", "$": "USD", "usd": "USD", "€": "EUR", "£": "GBP"}
PENDING = "pending"
CHANNEL_NAMES = {"email": "Email", "slack": "Slack", "sheet_row": "Sheet", "save_file": "Folder"}
SOURCE_UPDATES = {"sheet_event": "Sheet Updates", "new_email": "Emails", "form_submission": "Form Responses"}


class GenerationError(RuntimeError):
    pass


@dataclass
class Built:
    workflow: Workflow
    pending: set[str] = field(default_factory=set)


def generate_workflow(state: WorkflowState) -> Workflow:
    plan = build_plan(state.values(), state.intent.cardinality)
    readiness = evaluate(state, plan)
    if not readiness.ready:
        raise GenerationError(f"open fields: {[r.key for r in readiness.open_rows]}")
    built = _Builder(state, plan, preview=False).build()
    errors = validate_output(built.workflow, _grounded_values(state, plan))
    if errors:
        raise GenerationError("; ".join(errors))
    return built.workflow


def preview_workflow(state: WorkflowState) -> Built:
    """Partial workflow for the live diagram. Not validated: pending parts are expected."""
    return _Builder(state, build_plan(state.values(), state.intent.cardinality), preview=True).build()


class _Builder:
    def __init__(self, state: WorkflowState, plan: Plan, preview: bool) -> None:
        self.state, self.plan, self.preview = state, plan, preview
        self.values = state.values()
        self.nodes: list[WorkflowNode] = []
        self.edges: list[Edge] = []
        self.pending: set[str] = set()
        self._branch: str | None = None
        self._condition: str | None = None

    def build(self) -> Built:
        planned = [p for p in self.plan.nodes if p.emitted or (self.preview and p.emitted is None)]
        if self.preview and not any(p.node.category == "trigger" for p in planned):
            self._add(WorkflowNode(id="trigger_1", type="pending_trigger", name="Trigger", parameters={}), pending=True)
        for item in planned:
            self._add(self._node(item), pending=self._is_pending(item))
        if self.preview and not any(p.node.category == "action" for p in planned):
            self._add(WorkflowNode(id="action_1", type="pending_action", name="Action", parameters={}), pending=True)
        if self._condition:
            end = WorkflowNode(id="end_1", type="end", name="End", parameters={"reason": "condition not met"})
            self.nodes.append(end)
            self.edges.append(Edge(source=self._condition, target=end.id, branch="false"))
        return Built(Workflow(metadata=self._metadata(), nodes=self.nodes, edges=self.edges), self.pending)

    def _add(self, node: WorkflowNode, pending: bool) -> None:
        if self.nodes:
            self.edges.append(Edge(source=self.nodes[-1].id, target=node.id, branch=self._branch))
        self._branch = "true" if node.type == "condition" else None
        if node.type == "condition":
            self._condition = node.id
        if pending:
            self.pending.add(node.id)
        self.nodes.append(node)

    def _node(self, item: PlannedNode) -> WorkflowNode:
        node = item.node
        params = self._parameters(node)
        raw = {fd.name: str(self.values[fd.key]) for fd in node.fields if fd.key in self.values}
        workflow_type = fill_template(node.workflow_type or "", raw) or f"{PENDING}_{node.id}"
        return WorkflowNode(id=_node_id(node), type=workflow_type, name=self._name(node, params), parameters=params)

    def _parameters(self, node: NodeType) -> dict[str, Any]:
        applicable = set(self.plan.fields)
        params: dict[str, Any] = {
            PARAM_NAMES.get(fd.key, fd.name): self.values[fd.key]
            for fd in node.fields
            if fd in applicable and fd.key in self.values
        }
        _SPECIAL.get(node.id, lambda b, p: None)(self, params)
        if node.per_item and self.plan.node("loop"):
            params["for_each"] = "loop_1"
        return params

    def _name(self, node: NodeType, params: dict[str, Any]) -> str:
        intent = self.state.intent
        names = {
            **template_values(node, params),
            "Entity": (intent.main_entity or "document").title(),
            "Item": self._recipient().title(),
            "Recipients": plural(self._recipient()).title(),
            "Field": str(self.values.get("condition.field", "")).title(),
            "Condition_field": str(self.values.get("condition.field", "")).title(),
        }
        return fill_template(node.display_name, names) or node.label

    def _is_pending(self, item: PlannedNode) -> bool:
        if item.emitted is None:
            return True
        return any(
            fd in self.plan.fields and self.plan.is_required(fd) and not self.state.is_filled(fd.key)
            for fd in item.node.fields
        )

    def _recipient(self) -> str:
        return (self.state.intent.recipient_entity or "recipient").rstrip("s")

    def _metadata(self) -> WorkflowMetadata:
        trigger = next((n for n in self.plan.nodes if n.node.category == "trigger"), None)
        trigger_node = next((n for n in self.nodes if n.type.endswith("_trigger")), None)
        summary = summary_line(trigger.node, trigger_node.parameters) if trigger and trigger_node else ""
        return WorkflowMetadata(
            name=self._workflow_name(),
            trigger_summary=summary or (trigger_node.name if trigger_node else "Trigger not chosen yet"),
            description=" → ".join(n.name for n in self.nodes if n.type != "end"),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def _workflow_name(self) -> str:
        values, intent = self.values, self.state.intent
        entity = (intent.main_entity or "").title()
        channel = CHANNEL_NAMES.get(str(values.get("action.channel")), "")
        storage = display_value(FIELDS["save_file.storage"], values.get("save_file.storage")) or "Folder"
        updates = plural(entity) if entity else SOURCE_UPDATES.get(str(values.get("trigger.kind")), "Updates")
        names = {
            "notify": f"Save {updates} to {storage}" if channel == "Folder"
            else f"{entity or 'Event'} Alert via {channel or 'Notification'}",
            "send_document": f"Send {plural(entity or 'Document')} to {plural(self._recipient()).title()}",
            "report": f"Scheduled {entity or 'Summary'} Report",
            "backup": f"Back Up {display_value(FIELDS['file_source.storage'], values.get('file_source.storage')) or 'File'} Files"
            if self.plan.node("file_source") else f"Save {plural(entity) if entity else 'Files'} to {storage}",
            "sync_data": f"Copy Data to {display_value(FIELDS['sheet_row.platform'], values.get('sheet_row.platform')) or 'Destination'}",
            "post_message": f"Scheduled {channel or 'Message'} Post",
        }
        return names.get(str(values.get("goal.action")), "Automation")


def _node_id(node: NodeType) -> str:
    return "trigger_1" if node.category == "trigger" else f"{node.id}_1"


def _condition_params(builder: _Builder, params: dict[str, Any]) -> None:
    value = str(params.get("value", ""))
    number = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    if number and params.get("operator") in ("greater_than", "less_than", "equals", "not_equals"):
        params["numeric_value"] = float(number.group(0).replace(",", ""))
        currency = next((code for sym, code in CURRENCIES.items() if sym in value.lower()), None)
        if currency:
            params["currency"] = currency


def _extract_params(builder: _Builder, params: dict[str, Any]) -> None:
    wanted = [builder.state.intent.main_entity, builder.values.get("condition.field")]
    params["fields"] = [str(w) for w in wanted if w]
    params["from"] = "email body and attachments"


def _dedupe_params(builder: _Builder, params: dict[str, Any]) -> None:
    trigger = next((p.node.id for p in builder.plan.nodes if p.node.category == "trigger"), "")
    params["item"] = builder.state.intent.main_entity or "event"
    params["key"] = DEDUPE_KEYS.get(trigger, ["event_id"])


def _loop_params(builder: _Builder, params: dict[str, Any]) -> None:
    params["items"] = "recipients_1.rows"
    params["item"] = builder._recipient()


def _template_params(builder: _Builder, params: dict[str, Any]) -> None:
    variables = params.get("variables")
    if isinstance(variables, list):
        params["variables"] = dict(item.split("=", 1) for item in variables)


def _email_params(builder: _Builder, params: dict[str, Any]) -> None:
    values, context = builder.values, builder.plan.context
    if context.get("fact.per_record") and "recipients.email_column" in values:
        params["to"] = "{{item." + str(values["recipients.email_column"]) + "}}"
    elif values.get("recipients.source") == "trigger_data" and "recipients.email_field" in values:
        params["to"] = "{{trigger." + str(values["recipients.email_field"]) + "}}"
    body_nodes = {"template": "template_1", "summary": "summary_1"}
    mode = values.get("email.content_mode")
    if mode in body_nodes:
        params["body_from"] = body_nodes[str(mode)]
    if builder.plan.node("document") and builder.plan.node("document").emitted:  # type: ignore[union-attr]
        params["attachment"] = "{{document_1.file}}"
    if values.get("delivery.mode") == "immediate":
        params["delivery"] = "immediate"


_SPECIAL = {
    "condition": _condition_params, "extract": _extract_params, "dedupe": _dedupe_params,
    "loop": _loop_params, "template": _template_params, "email": _email_params,
}


def _grounded_values(state: WorkflowState, plan: Plan) -> dict[str, FieldValue]:
    """Values that must appear in the output: every collected field of every emitted node."""
    values = state.values()
    emitted = {p.node.id for p in plan.nodes if p.emitted}
    return {fd.key: values[fd.key] for fd in plan.fields if fd.node in emitted and fd.key in values}
