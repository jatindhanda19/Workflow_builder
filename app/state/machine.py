import re
from typing import Literal

from app.state.models import Mode, PreviousReason, WorkflowState

Answer = Literal["yes", "no", "unclear"]
YES = ("yes", "y", "yeah", "yep", "sure", "please do", "ok", "okay", "correct", "right")
NO = ("no", "n", "nope", "nah", "don't", "do not")

TURN_SCRATCH = {
    "plan": None,
    "picked": None,
    "llm_failed": False,
    "changes": [],
    "reply": None,
    "reply_parts": None,
    "reply_plan": None,
}


def mode_for_new_message(mode: Mode) -> Mode:
    return "post_generation" if mode == "ready" else mode


def yes_no(message: str) -> Answer:
    words = re.findall(r"[a-z']+", message.lower())
    if not words:
        return "unclear"
    if words[0] in YES:
        return "yes"
    if words[0] in NO or "keep" in words or "cancel" in words:
        return "no"
    if "start" in words or "new" in words:
        return "yes"
    return "unclear"


def start_fresh(state: WorkflowState, request: str) -> WorkflowState:
    """A clean state for a new automation. The chat transcript and every generated workflow are carried over."""
    fresh = new_workflow(state)
    fresh.messages, fresh.latest_user_message = list(state.messages), request
    return fresh


def new_workflow(state: WorkflowState) -> WorkflowState:
    """An empty draft and an empty chat; a generated workflow is kept under previous workflows."""
    previous, reasons = list(state.previous_workflows), previous_reasons(state)
    if state.workflow:
        previous, reasons = [*previous, state.workflow], [*reasons, "new_workflow"]
    return WorkflowState(turn=state.turn, previous_workflows=previous, previous_reasons=reasons)


def previous_reasons(state: WorkflowState) -> list[PreviousReason]:
    """One reason per previous workflow. Sessions saved before reasons were recorded lack them for their oldest
    entries; those count as earlier workflows."""
    count = len(state.previous_workflows)
    known = state.previous_reasons[-count:] if count else []
    return ["new_workflow"] * (count - len(known)) + list(known)


def as_update(state: WorkflowState) -> dict:
    """Every field of a state, for graph nodes that replace the whole state."""
    return {name: getattr(state, name) for name in WorkflowState.model_fields}
