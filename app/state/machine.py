"""Session modes and the transitions between them.

    COLLECTING ──(every app and required parameter known)──> READY
    READY ──(next user message)──> POST_GENERATION
    POST_GENERATION ──question──> POST_GENERATION (answered from the draft)
    POST_GENERATION ──edit──> values changed ──> COLLECTING or READY (regenerated)
    POST_GENERATION ──new request──> confirm ──yes──> fresh COLLECTING state
"""

import re
from typing import Literal

from app.state.models import Mode, WorkflowState

Answer = Literal["yes", "no", "unclear"]
YES = ("yes", "y", "yeah", "yep", "sure", "please do", "ok", "okay", "correct", "right")
NO = ("no", "n", "nope", "nah", "don't", "do not")

TURN_SCRATCH = {
    "plan": None,
    "picked": None,
    "llm_failed": False,
    "changes": [],
    "reply": None,
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
    """A clean state for a new automation. Only the chat transcript is carried over."""
    return WorkflowState(
        messages=list(state.messages), turn=state.turn, latest_user_message=request,
    )


def as_update(state: WorkflowState) -> dict:
    """Every field of a state, for graph nodes that replace the whole state."""
    return {name: getattr(state, name) for name in WorkflowState.model_fields}
