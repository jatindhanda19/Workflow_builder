import json
from dataclasses import dataclass
from enum import IntEnum
 
from app.agents.analyzer import format_history
from app.llm import LLMError, StructuredLLM
from app.prompts import load_prompt
from app.schemas.agent_schema import Ambiguity, AgentDecision
from app.state.workflow_state import WorkflowState
from app.templates.workflow_templates import FieldSpec, get_template
from app.validators.completeness import evaluate
 
OPENING_QUESTION = (
    "What would you like to automate? Tell me what should start the workflow "
    "and what should happen."
)
FALLBACK_QUESTION = "I still need a few details. Could you describe the missing parts of the workflow?"
RECENT_MESSAGES = 6
 
 
class Tier(IntEnum):
    AMBIGUITY = 1
    BLOCKING = 2
    DEPENDENT = 3
    REMAINING = 4
    OPTIONAL = 5
 
 
@dataclass(frozen=True)
class Candidate:
    field: str
    tier: Tier
    hint: str
    fallback_question: str
 
 
def pending_optional(state: WorkflowState) -> list[str]:
    return [
        key
        for key in state.relevant_optional
        if key not in state.collected and key not in state.asked_optional
    ]
 
 
def rank_candidates(state: WorkflowState) -> list[Candidate]:
    template = get_template(state.workflow_type)
    if template is None:
        return []
 
    collected = state.collected
    report = evaluate(state.workflow_type, collected, state.ambiguities)
    candidates: list[Candidate] = []
 
    for ambiguity in state.ambiguities:
        spec = template.field(ambiguity.field)
        if spec and _unlocked(spec, collected):
            candidates.append(_ambiguity_candidate(spec, ambiguity))
 
    for key in report.missing:
        spec = template.field(key)
        if spec is None or not _unlocked(spec, collected):
            continue
        if spec.blocking:
            tier = Tier.BLOCKING
        elif spec.depends_on:
            tier = Tier.DEPENDENT
        else:
            tier = Tier.REMAINING
        candidates.append(_missing_candidate(spec, tier))
 
    for key in pending_optional(state):
        spec = template.field(key)
        if spec:
            candidates.append(_missing_candidate(spec, Tier.OPTIONAL))
 
    order = {spec.key: index for index, spec in enumerate(template.fields)}
    return sorted(candidates, key=lambda c: (c.tier, order[c.field]))
 
 
def top_candidates(state: WorkflowState) -> list[Candidate]:
    ranked = rank_candidates(state)
    return [c for c in ranked if c.tier == ranked[0].tier] if ranked else []
 
 
def plan_next_question(llm: StructuredLLM, state: WorkflowState) -> AgentDecision:
    if state.workflow_type is None:
        return AgentDecision(
            target_field="workflow_type",
            question=OPENING_QUESTION,
            rationale="workflow type is not known yet",
        )
 
    candidates = top_candidates(state)
    if not candidates:
        return AgentDecision(target_field="", question=FALLBACK_QUESTION, rationale="no candidates")
 
    try:
        decision = llm.generate(
            AgentDecision, load_prompt("question_selector"), _build_context(state, candidates)
        )
    except LLMError:
        return _fallback(candidates[0])
 
    if decision.question.strip() and decision.target_field in {c.field for c in candidates}:
        return decision
    return _fallback(candidates[0])

def _unlocked(spec: FieldSpec, collected: dict) -> bool:
    return spec.depends_on is None or spec.depends_on in collected

def _ambiguity_candidate(spec: FieldSpec, ambiguity: Ambiguity) -> Candidate:
    hint = f'"{ambiguity.phrase}" is unclear: {ambiguity.reason}, Field meaning: {spec.description}'
    question = f'Could you be more specific about "{ambiguity.phrase}"? {spec.question}'
    return Candidate(spec.key, Tier.AMBIGUITY, hint, question)

def _missing_candidate(spec: FieldSpec, tier: Tier) -> Candidate:
    hint = f"missing.{spec.description}. Default question: {spec.question}"
    return Candidate(spec.key, tier, hint, spec.question)

def _fallback(candidate: Candidate) -> AgentDecision:
    return AgentDecision(
        target_field = candidate.field,
        question = candidate.fallback_question,
        rationale = "fallback question",
    )

def _build_context(state: WorkflowState, candidate: list[Candidate]) -> str:
    lines =[
        f"Workflow type: {state.workflow_type}",
        f"Original request: {state.original_request}",
        f"Already collected (never ask about these): {json.dumps(state.collected)}",
    ]
    if state.changes:
        lines.append("Changes made from the latest message: "+"; ".join(state.changes))
        lines.append("Candidates (choose exactly one target_field):")
        lines += [f"- {c.field} [{c.tier.name.lower()}]: {c.hint}" for c in candidate]
        lines.append(f"Recent conversation: \n{format_history(state.messages[-RECENT_MESSAGES])}")
        return "\n".join(lines)


