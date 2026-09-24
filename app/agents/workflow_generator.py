import json

from app.llm import StructuredLLM
from app.prompts import load_prompt
from app.schemas.workflow_schema import Workflow
from app.state.workflow_state import WorkflowState
from app.validators.completeness import evaluate

MAX_GENERATION_ATTEMPTS = 3

class IncompleteworkFlowError(RuntimeError):
    pass

def generate_workflow(llm: StructuredLLM, state: WorkflowState) -> Workflow:
    report = evaluate(state.workflow_type, state.collected, state.ambiguities)
    if not report.is_complete:
        problems = report.missing + report.invalid + report.unresolved
        raise IncompleteworkFlowError(f"cannot generate workflow, unresolved: {problems}")
    return llm.generate(Workflow, load_prompt("workflow_generator"), _build_context(state))

def _build_context(state: WorkflowState) -> str:
    sections =[
        f"Workflow type: {state.workflow_type}",
        f"Original request: {state.original_request}",
        f"Collected information:\n{json.dumps(state.collected, indent=2)}",
    ]
    if state.generation_errors:
        rejected = "\n".json(f"-{error}" for error in state.generation_error)
        sections.append(f"Your previous attempt was rejected for these reasons:\n{rejected}")
    return "\n\n".join(sections)

