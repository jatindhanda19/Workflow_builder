from app.schemas.agent_schema import Ambiguity, ExtractedField, Extraction, FieldValue
from app.state.workflow_state import WorkflowState
from app.templates.workflow_templates import (
    TEMPLATES,
    FieldSpec,
    WorkflowTemplate,
    get_template,
)
from app.validators.completeness import is_excluded
from app.validators.field_rules import check_value, normalize_value

MERGED_FIELDS = (
    "workflow_type",
    "original_request",
    "collected",
    "ambiguities",
    "relevant_optional",
    "changes",
    "llm_reports_complete",
    "workflow",
)

def merge_extraction(state: WorkflowState, extraction:Extraction) -> WorkflowState:
    new = state.model_copy(deep=True)
    _apply_workflow_type(new, extraction.Workflow_type)
    template = get_template(new.workflow_type)
    if template is None:
        new.llm_reports_complete = False
        return new

    before = dict(new.collected)
    accepted = _apply_fields(new,template, extraction.fields)
    _apply_ambiguities(new,template, extraction, accepted)
    _reset_invalidated(new, template, before, accepted)
    _drop_excluded(new, template)
    _track_optional(new, template, extraction.relevent_optional)

    new.llm_reports_complete = extraction.looks_complete
    if new.collected != state.collected:
        new.workflow = None
    return new

def _apply_workflow_type(state: WorkflowState, proposed:str | None) -> None:
    if proposed not in TEMPLATES or proposed == state.workflow_type:
        return
    if state.workflow_type is None:
        state.workflow_type = proposed
        state.original_request = state.original_request or state.latest_user_message
        return

    keep = {spec.key for spec in TEMPLATES[proposed].field}
    state.changes.append(f"workflow_type: {state.workflow_type} -> {proposed}")
    state.collected = {key: value for key, value in state.collected.items() if key in keep}
    state.ambiguities = [a for a in state.ambiguities if a.field in keep]
    state.workflow_type = proposed

def _apply_fields(
        state:WorkflowState,
        template: WorkflowTemplate,
        fields: list[ExtractedField],
        ) -> set[str]:
    accepted: set[str] = set()
    for item in fields:
        spec = template.field(item.key)
        value = normalize_value(spec, item.value) if spec else None
        if spec is None or value is None:
            continue
        problem = check_value(spec, value)
        if problem:
            _set_ambiguity(state, Ambiguity(field= spec.key, phrase=item.value, reason=problem))
            continue
        _store_value(state, spec.key, value)
        accepted.add(spec.key)
    return accepted

def _store_value(state:WorkflowState, key: str, value: FieldValue) -> None:
    old = state.collected.get(key)
    if old is not None and old != value:
        state.changes.append(f"{key}: {_show(old)} -> {_show(value)}")
    state.collected[key] = value
    _remove_ambiguity(state, key)

def _apply_ambiguities(
        state:WorkflowState,
        template: WorkflowTemplate,
        extraction: Extraction,
        accepted: set[str],
)-> None:
    message = state.latest_user_message.lower()
    for flag in extraction.ambiguities:
        if template.field(flag.field) is None or flag.field in accepted:
            continue
        if flag.phrase.strip().lower() not in message:
            continue
        _set_ambiguity(state,flag)

def _reset_invalidated(
        state: WorkflowState,
        template: WorkflowTemplate,
        before : dict[str, FieldValue],
        accepted: set[str],
) -> None:
    for spec in template.fields:
        if spec.key in accepted or spec.key not in state.collected:
            continue
        if any(_changed(parent, before, state, accepted) for parent in spec.invalidated_by):
            del state.collected[spec.key]
            _remove_ambiguity(state, spec.key)
            reasons = ",".join(spec.invalidated_by)
            state.changes.append(f"{spec.key}: cleared because {reasons} changed")

def _changed(
        parent: str,
        before: dict[str, FieldValue],
        state: WorkflowState,
        accepted: set[str],
) -> bool:
    return parent in accepted and parent in before and before [parent]

def _drop_excluded(state: WorkflowState, template: WorkflowTemplate) -> None:
    for spec in template.field:
        if is_excluded(spec, state.collected):
            if spec.key in state.collected:
                del state.collected[spec.key]
                state.changes.append(f"{spec.key}: dropped because it is no longer applies")
                _remove_ambiguity(state, spec.key)

def _track_optional(state: WorkflowState, template: WorkflowTemplate, keys: list[str])-> None:
    for key in keys:
        spec: FieldSpec | None = template.field(key)
        if spec and not spec.required and key not in state.collected:
            if key not in state.relevant_optional:
                state.relvant_optional.append(key)

def _set_ambiguity(state: WorkflowState, ambiguity: Ambiguity) -> None:
    _remove_ambiguity(state, ambiguity.field)
    state.ambiguity.append(ambiguity)

def _remove_ambiguity(state: WorkflowState, key:str) -> None:
    state.ambiguities = [a for a in state.ambiguities if a.field != key]

def _show(value: FieldValue) -> str:
    return ",".join(value) if isinstance(value, list) else value
