import json

from app.llm import StructuredLLM
from app.prompts import load_prompt
from app.schemas.agent_schema import Extraction
from app.state.workflow_state import Message, WorkflowState
from app.templates.workflow_templates import describe_templates

RECENT_MESSAGES = 6

def analyze_message(llm: StructuredLLM, state: WorkflowState) -> Extraction:
    return llm.generate(Extraction, load_prompt("analyzer"), build_context(state))

def build_context(state: WorkflowState) -> str:
    ambiguities = [f'{a.field}:"{a.phrase}"({a.reason})' for a in state.ambiguities]
    sections = [f"Templates: \n {describe_templates()}",
               f"Current workflow type: {state.workflow_type or 'not decided'}",
               f"Already collected: {json.dumps(state.collected)}",
               f"Open ambiguities: {';'.join(ambiguities) or 'none'}",
               f"Assistant's last question:{state.next_question or 'none'}",
               f"(filed: {state.target_field or 'none'})",
               f"Recent conversation: \n{format_history(state.messages[:-1][-RECENT_MESSAGES: ])}",
               f"Latest user message: \n{state.latest_user_message}",
              ]
    return "\n\n".join(sections)

def format_history(messages: list[Message]) -> str:
    return "\n".join(f"{m.role}:{m.content}" for m in messages) or "(start of conversation)"
