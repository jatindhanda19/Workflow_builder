import json

from app.llm import StructuredLLM
from app.prompts import load_prompt
from app.registry.node_types import FIELDS, describe_registry
from app.registry.selection import active_fields
from app.schemas.agent_schema import Extraction
from app.state.workflow_state import Message, WorkflowState

RECENT_MESSAGES = 6
LATEST_MARKER = "LATEST USER MESSAGE:"


def extract(llm: StructuredLLM, state: WorkflowState) -> Extraction:
    return llm.generate(Extraction, load_prompt("extractor"), build_context(state))


def build_context(state: WorkflowState) -> str:
    values = state.values()
    open_fields = [
        f"{fdef.key} ({state.fields[fdef.key].status}: {state.fields[fdef.key].reason})"
        if fdef.key in state.fields
        else f"{fdef.key} (missing)"
        for fdef in active_fields(values)
        if fdef.key not in values
    ]
    target = FIELDS.get(state.target_field or "")
    sections = [
        f"FIELD CATALOGUE:\n{describe_registry()}",
        f"ALREADY FILLED: {json.dumps(values, ensure_ascii=False)}",
        f"OPEN FIELDS: {'; '.join(open_fields) or 'none'}",
        f"KNOWN TRIGGER KIND: {state.trigger_kind or 'unknown'}",
        f"ASSISTANT'S LAST QUESTION WAS ABOUT: {target.key if target else 'nothing yet'}",
        f"RECENT CONVERSATION:\n{format_history(state.messages[:-1][-RECENT_MESSAGES:])}",
        f"{LATEST_MARKER}\n{state.latest_user_message}",
    ]
    return "\n\n".join(sections)


def format_history(messages: list[Message]) -> str:
    return "\n".join(f"{m.role}: {m.content}" for m in messages) or "(start of conversation)"
