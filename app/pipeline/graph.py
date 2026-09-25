"""One conversation turn as a LangGraph state graph.

ingest ─┬─> confirm ─┬─> classify / followup / check / respond
        ├─> followup ─┬─> respond                 (question answered, new request to confirm)
        │             └─> extract                 (edit)
        ├─> classify ──> extract                  (goal still unknown: intent first)
        └─> extract ──> merge ─┬─> check ─┬─> ask ──────> respond
                               │          ├─> generate ─> respond
                               │          └─> respond
                               └─> respond        (new request needs confirmation)
"""

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.pipeline.extraction.extract import extract as run_extractor
from app.pipeline.generation.builder import GenerationError, generate_workflow
from app.pipeline.intent.classifier import classify_intent
from app.pipeline.intent.followup import classify_followup
from app.core.llm import LLMError, StructuredLLM
from app.pipeline.planning import build_plan
from app.registry.display import display_value
from app.registry import FIELDS
from app.pipeline.questions.ack import acknowledgement
from app.pipeline.questions.selector import base_question, phrase_question, select_target, target_detail
from app.state.machine import TURN_SCRATCH, as_update, mode_for_new_message, start_fresh, yes_no
from app.state.models import LogEntry, Message, WorkflowState
from app.state.updater import merge_turn, reopen
from app.pipeline.validation.readiness import evaluate

logger = logging.getLogger(__name__)

GENERIC_ERROR = "Sorry, I had trouble reading that message."
READY_REPLY = "Great! I have all the required information. Generating your workflow…"
GENERATION_FAILED = "I have every detail, but the workflow did not pass its final checks. Could you tell me what to change?"
NEW_REQUEST_CONFIRM = "That sounds like a new automation. Should I start a new workflow for it? The current one{name} will be discarded. (yes/no)"
KEEP_CURRENT = "Okay, I'll keep the current workflow."
NOTHING_TO_EDIT = "I couldn't tell what to change. Which parameter should I update, and to what? (for example: change the recipient to a@b.com)"
MIN_FILLED_FOR_CONFIRM = 2


def _route(state: WorkflowState) -> str:
    return state.reply_route or "respond"


def build_graph(llm: StructuredLLM):
    def ingest(state: WorkflowState) -> dict[str, Any]:
        mode = mode_for_new_message(state.mode)
        if state.pending_new_request:
            route = "confirm"
        elif mode == "post_generation":
            route = "followup"
        else:
            route = "classify" if not state.is_filled("goal.action") else "extract"
        return {
            **TURN_SCRATCH,
            "messages": [*state.messages, Message(role="user", content=state.latest_user_message)],
            "turn": state.turn + 1,
            "mode": mode,
            "original_request": state.original_request or state.latest_user_message,
            "reply_route": route,
        }

    def confirm(state: WorkflowState) -> dict[str, Any]:
        answer = yes_no(state.latest_user_message)
        if answer == "yes":
            fresh = start_fresh(state, state.pending_new_request or state.latest_user_message)
            fresh.log.append(LogEntry(turn=fresh.turn, field="*", event="cleared", detail="new workflow started"))
            return {**as_update(fresh), "reply_route": "classify"}
        if answer == "no":
            return {"pending_new_request": None, "reply": KEEP_CURRENT,
                    "reply_route": "respond" if state.mode == "post_generation" else "check"}
        return {"pending_new_request": None, "reply_route": "followup" if state.mode == "post_generation" else "extract"}

    def classify(state: WorkflowState) -> dict[str, Any]:
        return {"intent_result": classify_intent(llm, state.latest_user_message)}

    def followup(state: WorkflowState) -> dict[str, Any]:
        rows = evaluate(state, build_plan(state.values(), state.intent.cardinality)).rows
        workflow = state.workflow.model_dump(by_alias=True) if state.workflow else None
        decision = classify_followup(llm, state.latest_user_message, rows, workflow)
        if decision.kind == "edit":
            return {"followup": decision, "edit_mode": True, "reply_route": "extract"}
        if decision.kind == "new_request":
            return {"followup": decision, "pending_new_request": state.latest_user_message,
                    "reply": _confirm_text(state), "reply_route": "respond"}
        answer = (decision.answer or "").strip() if decision.kind == "question" else ""
        return {"followup": decision, "reply": answer or workflow_summary(state), "reply_route": "respond"}

    def extract(state: WorkflowState) -> dict[str, Any]:
        plan = build_plan(state.values(), state.intent.cardinality)
        try:
            return {"extraction": run_extractor(llm, state, plan)}
        except LLMError:
            logger.exception("extraction failed after retries")
            return {"extraction": None, "llm_failed": True}

    def merge(state: WorkflowState) -> dict[str, Any]:
        extraction = state.extraction
        filled = sum(1 for e in state.fields.values() if e.status == "filled" and e.source == "user")
        if extraction and extraction.is_new_request and not state.edit_mode and filled >= MIN_FILLED_FOR_CONFIRM:
            return {"pending_new_request": state.latest_user_message, "reply": _confirm_text(state), "reply_route": "respond"}
        fallback = {}
        if state.edit_mode and state.followup and state.followup.value and len(state.followup.fields) == 1:
            fallback = {state.followup.fields[0]: state.followup.value}
        merged = merge_turn(state, state.intent_result, extraction, overwrite=state.edit_mode, fallback_values=fallback)
        if state.edit_mode and state.followup:
            changed = {i.field for i in merged.log if i.turn == merged.turn and i.event in ("filled", "overwritten")}
            reopen(merged, [k for k in state.followup.fields if k not in changed])
        update = as_update(merged)
        understood = any(item.turn == merged.turn for item in merged.log) or merged.conflicts
        if state.llm_failed and not understood:
            update["reply"] = GENERIC_ERROR
        update["reply_route"] = "check"
        return update

    def check(state: WorkflowState) -> dict[str, Any]:
        readiness = evaluate(state, build_plan(state.values(), state.intent.cardinality))
        if not readiness.ready:
            return {"reply_route": "ask"}
        if state.workflow is None:
            return {"reply_route": "generate"}
        reply = NOTHING_TO_EDIT if state.edit_mode else workflow_summary(state)
        return {"mode": "ready", "reply": reply, "reply_route": "respond"}

    def ask(state: WorkflowState) -> dict[str, Any]:
        plan = build_plan(state.values(), state.intent.cardinality)
        target = select_target(state, plan)
        if target is None:
            return {"reply_route": "respond"}
        question = phrase_question(llm, state, target, base_question(state, target))
        first_question = not any(item.event == "asked" for item in state.log)
        ack = acknowledgement(state, first_question) if target.kind != "conflict" else ""
        text = " ".join(part for part in (state.reply, ack, question) if part)
        log = [*state.log, LogEntry(turn=state.turn, field=target.key, event="asked", detail=target_detail(state, target))]
        return {"mode": "collecting", "target_field": target.key, "target_kind": target.kind, "log": log,
                "reply": text, "reply_route": "respond"}

    def generate(state: WorkflowState) -> dict[str, Any]:
        try:
            workflow = generate_workflow(state)
        except GenerationError:
            logger.exception("generated workflow failed its checks")
            return {"reply": GENERATION_FAILED, "reply_route": "respond"}
        prefix = "Updated " + "; ".join(state.changes) + ". " if state.edit_mode and state.changes else ""
        chain = " → ".join(n.name for n in workflow.nodes if n.type != "end")
        reply = (f"{prefix}{READY_REPLY}\n\nYour workflow \"{workflow.name}\" is ready: {chain}. "
                 "The diagram, table and JSON are on the right. Ask me anything about it, tell me what to change, "
                 "or describe a new automation.")
        return {"workflow": workflow, "mode": "ready", "target_field": None, "target_kind": None,
                "reply": reply, "reply_route": "respond"}

    def respond(state: WorkflowState) -> dict[str, Any]:
        return {"messages": [*state.messages, Message(role="assistant", content=state.reply or "")], "reply_route": None}

    graph = StateGraph(WorkflowState)
    for name, fn in (("ingest", ingest), ("confirm", confirm), ("classify", classify), ("followup", followup),
                     ("extract", extract), ("merge", merge), ("check", check), ("ask", ask),
                     ("generate", generate), ("respond", respond)):
        graph.add_node(name, fn)
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", _route, ["confirm", "followup", "classify", "extract"])
    graph.add_conditional_edges("confirm", _route, ["classify", "followup", "extract", "check", "respond"])
    graph.add_edge("classify", "extract")
    graph.add_conditional_edges("followup", _route, ["extract", "respond"])
    graph.add_edge("extract", "merge")
    graph.add_conditional_edges("merge", _route, ["check", "respond"])
    graph.add_conditional_edges("check", _route, ["ask", "generate", "respond"])
    graph.add_edge("ask", "respond")
    graph.add_edge("generate", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def workflow_summary(state: WorkflowState) -> str:
    """A plain answer from state, used when the follow-up LLM gives no answer."""
    rows = evaluate(state, build_plan(state.values(), state.intent.cardinality)).rows
    lines = [f"- **{r.label}:** {display_value(FIELDS[r.key], r.value)}" for r in rows if r.status == "filled"]
    head = f'Your workflow "{state.workflow.name}" runs: {state.workflow.metadata.description}.' if state.workflow else "Here is what I have so far:"
    return "\n\n".join([head, "\n".join(lines), "Tell me anything you want to change, or describe a new automation."])


def _confirm_text(state: WorkflowState) -> str:
    return NEW_REQUEST_CONFIRM.format(name=f' ("{state.workflow.name}")' if state.workflow else "")


def run_turn(graph, state: WorkflowState, user_message: str) -> WorkflowState:
    result = graph.invoke(state.model_copy(update={"latest_user_message": " ".join(user_message.split())}))
    return WorkflowState.model_validate(result)
