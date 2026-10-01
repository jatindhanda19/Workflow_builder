"""The AI step of each turn: design the workflow (any app, n8n-style) and predict the next MCQ."""

import json

from app.core.llm import StructuredLLM
from app.pipeline.planner_schema import TurnPlan
from app.state.models import Message, WorkflowState

RECENT_MESSAGES = 8
# Defaults are left out of the draft to save tokens; this note states them once.
DRAFT_DEFAULTS = "(parameters omit kind=text, required=true and empty values)"


def plan_turn(llm: StructuredLLM, state: WorkflowState) -> TurnPlan:
    return llm.generate(TurnPlan, "planner", build_context(state))


def build_context(state: WorkflowState) -> str:
    draft = [step.model_dump(exclude_none=True, exclude_defaults=True) | {"kind": step.kind} for step in state.steps]
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
    return "\n\n".join(sections)


def format_history(messages: list[Message]) -> str:
    lines = [f"{m.role}: {m.content}" + (f" [options: {' | '.join(m.options)}]" if m.options else "") for m in messages]
    return "\n".join(lines) or "(start of conversation)"
