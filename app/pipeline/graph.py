"""One conversation turn as a LangGraph.

    ingest ─┬─> confirm (a new automation was proposed: start over?) ─┬─> plan
            │                                                          └─> check / respond
            └─> plan (LLM: design the workflow for any app, predict the next MCQ)
                  └─> merge (code: ground, validate, keep earlier answers)
                        ├─> respond (an unclear message: ask which reading was meant, change nothing)
                        └─> check (code: is every app and required parameter known?)
                              ├─ no  ─> ask      (the MCQ)
                              └─ yes ─> generate (workflow JSON, no LLM)
                                          └─> respond
"""

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.llm import LLMError, RateLimitedError, StructuredLLM
from app.pipeline.generation.builder import GenerationError, generate_workflow, matches_draft
from app.pipeline.merge import is_placeholder, merge_turn
from app.pipeline.planner import plan_turn
from app.pipeline.questions import (
    CLARIFY_TARGET,
    clarify_question,
    confirm_new_request_question,
    match_option,
    next_question,
    render,
)
from app.pipeline.validation import display, is_ready
from app.state.machine import TURN_SCRATCH, as_update, mode_for_new_message, start_fresh, yes_no
from app.state.models import LogEntry, Message, WorkflowState

logger = logging.getLogger(__name__)

AI_UNAVAILABLE = "Sorry, I couldn't reach the AI service to read that message. Please try again."
RATE_LIMITED = ("The AI service has reached its usage limit for now, so I couldn't read that message. "
                "Please try again in {wait}. Your answers so far are kept.")
READY_REPLY = "Great! I have everything I need. Generating your workflow…"
GENERATION_FAILED = "I have every detail, but the workflow did not pass its final checks. Could you tell me what to change?"
NEW_REQUEST_CONFIRM = ("That sounds like a new automation. Should I start a new workflow for it? "
                       "The current one{name} will be {fate}.")
KEEP_CURRENT = "Okay, I'll keep the current workflow."
NOTHING_CHANGED = "I couldn't tell what to change. Which step or setting should I update, and to what?"


def _route(state: WorkflowState) -> str:
    return state.reply_route or "respond"


def build_graph(llm: StructuredLLM):
    def ingest(state: WorkflowState) -> dict[str, Any]:
        picked = match_option(state.question, state.latest_user_message)
        # A picked option ("B", "2" or its label) is stored as its label, so every later step reads one answer.
        message = picked or state.latest_user_message
        return {
            **TURN_SCRATCH,
            "latest_user_message": message,
            "picked": picked,
            "question": None,
            "messages": [*state.messages, Message(role="user", content=message)],
            "turn": state.turn + 1,
            "mode": mode_for_new_message(state.mode),
            "reply_route": "confirm" if state.pending_new_request else "plan",
        }

    def confirm(state: WorkflowState) -> dict[str, Any]:
        answer = yes_no(state.latest_user_message)
        if answer == "yes":
            fresh = start_fresh(state, state.pending_new_request or state.latest_user_message)
            fresh.log.append(LogEntry(turn=fresh.turn, field="*", event="cleared", detail="new workflow started"))
            return {**as_update(fresh), "reply_route": "plan"}
        if answer == "no":
            return {"pending_new_request": None, "reply": KEEP_CURRENT,
                    "reply_route": "respond" if state.mode == "post_generation" else "check"}
        return {"pending_new_request": None, "reply_route": "plan"}

    def plan(state: WorkflowState) -> dict[str, Any]:
        try:
            return {"plan": plan_turn(llm, state)}
        except RateLimitedError as exc:
            logger.warning("planning skipped: every model is rate limited (retry in %s)", exc.wait_text)
            return {"plan": None, "llm_failed": True, "reply": RATE_LIMITED.format(wait=exc.wait_text)}
        except LLMError:
            logger.exception("planning failed after retries")
            return {"plan": None, "llm_failed": True, "reply": AI_UNAVAILABLE}

    def merge(state: WorkflowState) -> dict[str, Any]:
        result = state.plan
        kind = result.message_kind if result else "build"
        if kind == "new_request" and (state.has_answers() or state.workflow) and not state.picked:
            # Only a generated workflow is kept; answers to an unfinished one are dropped.
            text = NEW_REQUEST_CONFIRM.format(name=f' ("{state.workflow.name}")' if state.workflow else "",
                                              fate="saved under Previous workflows" if state.workflow else "discarded")
            question = confirm_new_request_question(text)
            return {"pending_new_request": state.latest_user_message, "question": question,
                    "reply": render(question), "reply_route": "respond"}
        if kind == "unclear" and state.steps and not state.picked:
            # Several readings: nothing from the plan is merged, the user picks the one they meant.
            question = clarify_question(state, result.next_question)
            log = [*state.log, LogEntry(turn=state.turn, field=CLARIFY_TARGET, event="asked")]
            return {"target": CLARIFY_TARGET, "question": question, "log": log,
                    "reply": render(question), "reply_route": "respond"}
        answer = (result.answer or "").strip() if result and kind == "question" else ""
        if kind in ("question", "other") and state.mode == "post_generation" and not state.picked:
            # After generation a question never edits the workflow.
            return {"reply": answer or workflow_summary(state), "reply_route": "respond"}
        # While collecting, the plan is always merged: merge keeps only what the user really said.
        merged = merge_turn(state, result)
        if state.llm_failed and not merged.changes:
            # Nothing was read: show only the error, so the user retries their message instead of a new question.
            return {"reply": state.reply or AI_UNAVAILABLE, "reply_route": "respond"}
        update = as_update(merged)
        if state.llm_failed:
            update["reply"] = None  # a picked option was still applied in code
        elif answer:
            update["reply"] = answer
        elif _structure(merged) != _structure(state) and not all(is_placeholder(s) for s in merged.steps):
            update["reply"] = _plan_text(merged)
        update["reply_route"] = "check"
        return update

    def check(state: WorkflowState) -> dict[str, Any]:
        if not is_ready(state):
            return {"reply_route": "ask"}
        if not matches_draft(state.workflow, state):
            return {"reply_route": "generate"}
        reply = NOTHING_CHANGED if state.mode == "post_generation" else workflow_summary(state)
        return {"mode": "ready", "reply": state.reply or reply, "reply_route": "respond"}

    def ask(state: WorkflowState) -> dict[str, Any]:
        predicted = next_question(state, state.plan.next_question if state.plan else None)
        if predicted is None:
            return {"reply_route": "respond"}
        target, question = predicted
        ack = f"Got it: {'; '.join(state.changes)}." if state.changes else ""
        lead = " ".join(part for part in (ack, state.reply) if part)
        text = "\n\n".join(part for part in (lead, render(question)) if part)
        log = [*state.log, LogEntry(turn=state.turn, field=target, event="asked")]
        return {"mode": "collecting", "target": target, "question": question, "log": log,
                "reply": text, "reply_route": "respond"}

    def generate(state: WorkflowState) -> dict[str, Any]:
        try:
            workflow = generate_workflow(state)
        except GenerationError:
            logger.exception("generated workflow failed its checks")
            return {"reply": GENERATION_FAILED, "reply_route": "respond"}
        # An edit, even one that took several turns, replaces the earlier version, which is kept.
        previous = [*state.previous_workflows, state.workflow] if state.workflow else state.previous_workflows
        prefix = "Updated " + "; ".join(state.changes) + ". " if state.workflow and state.changes else ""
        chain = " → ".join(n.name for n in workflow.nodes if n.type != "end")
        reply = (f"{prefix}{READY_REPLY}\n\nYour workflow \"{workflow.name}\" is ready: {chain}. "
                 "The diagram and JSON are below. Ask me anything about it, tell me what to change, "
                 "or describe a new automation.")
        return {"workflow": workflow, "previous_workflows": previous, "mode": "ready", "target": None, "reply": reply,
                "reply_route": "respond"}

    def respond(state: WorkflowState) -> dict[str, Any]:
        options = [o.label for o in state.question.options] if state.question else []
        message = Message(role="assistant", content=state.reply or "", options=options)
        return {"messages": [*state.messages, message], "reply_route": None}

    graph = StateGraph(WorkflowState)
    for name, fn in (("ingest", ingest), ("confirm", confirm), ("plan", plan), ("merge", merge), ("check", check),
                     ("ask", ask), ("generate", generate), ("respond", respond)):
        graph.add_node(name, fn)
    graph.add_edge(START, "ingest")
    graph.add_conditional_edges("ingest", _route, ["confirm", "plan"])
    graph.add_conditional_edges("confirm", _route, ["plan", "check", "respond"])
    graph.add_edge("plan", "merge")
    graph.add_conditional_edges("merge", _route, ["check", "respond"])
    graph.add_conditional_edges("check", _route, ["ask", "generate", "respond"])
    graph.add_edge("ask", "respond")
    graph.add_edge("generate", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def workflow_summary(state: WorkflowState) -> str:
    """A plain description of the workflow from state."""
    lines = []
    for step in state.steps:
        values = ", ".join(f"{p.label}: {display(p, p.value)}" for p in step.params if p.value is not None)
        lines.append(f"- **{_step_label(step)}**" + (f" ({values})" if values else ""))
    head = f'Your workflow "{state.workflow.name}" runs: {state.workflow.metadata.description}.' if state.workflow else "Here is what I have so far:"
    return "\n\n".join([head, "\n".join(lines), "Tell me anything you want to change, or describe a new automation."])


def _structure(state: WorkflowState) -> list[tuple[str, str | None]]:
    return [(s.id, s.app) for s in state.steps]


def _plan_text(state: WorkflowState) -> str:
    roles = {"trigger": "When", "condition": "If", "action": "Then"}
    lines = [f"{i}. **{roles[s.kind]}:** {_step_label(s)}" for i, s in enumerate(state.steps, 1)]
    return "Here's the plan:\n\n" + "\n".join(lines)


def _step_label(step) -> str:
    return step.title if step.app else f"{step.operation} _(app to choose)_"


def run_turn(graph, state: WorkflowState, user_message: str) -> WorkflowState:
    result = graph.invoke(state.model_copy(update={"latest_user_message": " ".join(user_message.split())}))
    return WorkflowState.model_validate(result)
