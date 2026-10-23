"""The AI step of each turn: design the workflow (any app, n8n-style) and predict the next MCQ."""

import json

from app.core.llm import LLMCall, StructuredLLM
from app.pipeline.planner_schema import TurnPlan
from app.security.pii import ID_NUMBERS, redact_pii
from app.state.models import PLACEHOLDER_OPERATIONS, Message, Step, WorkflowState

RECENT_MESSAGES = 8
# Defaults are left out of the draft to save tokens; this note states them once.
DRAFT_DEFAULTS = ("(parameters omit kind=text, required=true and empty values; "
                  "a step without an operation still needs one)")


def plan_turn(llm: StructuredLLM, state: WorkflowState, call: LLMCall | None = None) -> TurnPlan:
    return llm.generate(TurnPlan, "planner", build_context(state), call=call)


def build_context(state: WorkflowState) -> str:
    draft = [_draft_step(step) for step in state.steps]
    picked = f'yes, the user picked the option "{state.picked}"' if state.picked else "no, the user typed a reply"
    sections = [
        f"MODE: {'editing a generated workflow' if state.mode == 'post_generation' else 'collecting'}",
        f"CURRENT DRAFT ({state.name or 'unnamed'}) {DRAFT_DEFAULTS}:\n"
        f"{json.dumps(draft, ensure_ascii=False, separators=(',', ':')) if draft else '(empty)'}",
        f"LAST QUESTION WAS ABOUT: {state.target or 'nothing yet'}",
        f"ANSWER IS A PICKED OPTION: {picked}",
        f"RECENT CONVERSATION:\n{format_history(state.messages[:-1][-RECENT_MESSAGES:])}",
        f"LATEST USER MESSAGE:\n{state.latest_user_message}",
    ]
    # Identity numbers never leave the server. Emails and phone numbers stay: a workflow may need them.
    return redact_pii("\n\n".join(sections), ID_NUMBERS)


def _draft_step(step: Step) -> dict:
    data = step.model_dump(exclude_none=True, exclude_defaults=True) | {"kind": step.kind}
    if step.operation in PLACEHOLDER_OPERATIONS:
        # Shown, the placeholder ("Do something with the result") was copied back as the step's operation.
        data.pop("operation", None)
    return data


def format_history(messages: list[Message]) -> str:
    lines = [f"{m.role}: {m.content}" + (f" [options: {' | '.join(m.options)}]" if m.options else "") for m in messages]
    return "\n".join(lines) or "(start of conversation)"
