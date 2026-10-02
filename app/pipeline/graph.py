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
from app.pipeline.merge import is_placeholder, merge_turn, typed_app_answer
from app.pipeline.planner import plan_turn
from app.pipeline.questions import (
    CLARIFY_TARGET,
    clarify_question,
    confirm_new_request_question,
    match_option,
    next_question,
    render,
)
from app.pipeline.validation import display, is_ready, links, structure_errors
from app.state.machine import TURN_SCRATCH, as_update, mode_for_new_message, previous_reasons, start_fresh, yes_no
from app.state.models import LogEntry, Message, PlanRow, Question, ReplyParts, WorkflowState

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
NEXT_STEPS = "Tell me anything you want to change, or describe a new automation."


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
        if kind == "unclear" and state.steps and not state.picked and not typed_app_answer(state):
            # Several readings: nothing from the plan is merged, the user picks the one they meant.
            question = clarify_question(state, result.next_question)
            log = [*state.log, LogEntry(turn=state.turn, field=CLARIFY_TARGET, event="asked")]
            return {"target": CLARIFY_TARGET, "question": question, "log": log,
                    "reply": render(question), "reply_route": "respond"}
        answer = (result.answer or "").strip() if result and kind == "question" else ""
        if kind in ("question", "other") and state.mode == "post_generation" and not state.picked:
            # After generation a question never edits the workflow.
            if answer:
                return {"reply": answer, "reply_route": "respond"}
            return {"reply": workflow_summary(state), "reply_parts": workflow_summary_parts(state),
                    "reply_route": "respond"}
        # While collecting, the plan is always merged: merge keeps only what the user really said.
        merged = merge_turn(state, result)
        handled = merged.changes or any(e.turn == merged.turn and e.field == state.target and e.event == "rejected"
                                        for e in merged.log)
        if state.llm_failed and not handled:
            # Nothing was read: show only the error, so the user retries their message instead of a new question.
            return {"reply": state.reply or AI_UNAVAILABLE, "reply_route": "respond"}
        update = as_update(merged)
        if state.llm_failed:
            update["reply"] = None  # a picked option was still applied in code
        elif answer:
            update["reply"] = answer
        elif _structure(merged) != _structure(state) and not all(is_placeholder(s) for s in merged.steps):
            update["reply"], update["reply_plan"] = _plan_text(merged), _plan_rows(merged)
        update["reply_route"] = "check"
        return update

    def check(state: WorkflowState) -> dict[str, Any]:
        if not is_ready(state):
            return {"reply_route": "ask"}
        if not matches_draft(state.workflow, state):
            return {"reply_route": "generate"}
        if state.reply or state.mode == "post_generation":
            return {"mode": "ready", "reply": state.reply or NOTHING_CHANGED, "reply_route": "respond"}
        return {"mode": "ready", "reply": workflow_summary(state), "reply_parts": workflow_summary_parts(state),
                "reply_route": "respond"}

    def ask(state: WorkflowState) -> dict[str, Any]:
        predicted = next_question(state, state.plan.next_question if state.plan else None)
        problems = structure_errors(state) if predicted is None else []
        if problems:
            # Every value is known but a condition is not set up as the user said: ask about it, don't guess.
            predicted = CLARIFY_TARGET, Question(target=CLARIFY_TARGET, text=problems[0])
        if predicted is None:
            return {"reply_route": "respond"}
        target, question = predicted
        ack = f"Got it: {'; '.join(state.changes)}." if state.changes else ""
        lead = " ".join(part for part in (ack, state.reply) if part)
        text = "\n\n".join(part for part in (lead, render(question)) if part)
        log = [*state.log, LogEntry(turn=state.turn, field=target, event="asked")]
        parts = ReplyParts(saved=_saved(state), lead="Here's the plan:" if state.reply_plan else state.reply,
                           plan=state.reply_plan or [], context=question.step, note=question.note, text=question.text)
        return {"mode": "collecting", "target": target, "question": question, "log": log,
                "reply": text, "reply_parts": parts, "reply_route": "respond"}

    def generate(state: WorkflowState) -> dict[str, Any]:
        try:
            workflow = generate_workflow(state)
        except GenerationError:
            logger.exception("generated workflow failed its checks")
            return {"reply": GENERATION_FAILED, "reply_route": "respond"}
        # An edit, even one that took several turns, replaces the earlier version, which is kept.
        previous = [*state.previous_workflows, state.workflow] if state.workflow else state.previous_workflows
        prefix = "Updated " + "; ".join(state.changes) + ". " if state.workflow and state.changes else ""
        chain = workflow.metadata.description
        reply = (f"{prefix}{READY_REPLY}\n\nYour workflow \"{workflow.name}\" is ready: {chain}. "
                 "The diagram and JSON are below. Ask me anything about it, tell me what to change, "
                 "or describe a new automation.")
        parts = ReplyParts(saved=_saved(state), text=f'Your workflow "{workflow.name}" is ready.',
                           more=f"{chain}. Ask me anything about it, tell me what to change, or describe a new automation.")
        reasons = [*previous_reasons(state), "edited"] if state.workflow else previous_reasons(state)
        return {"workflow": workflow, "previous_workflows": previous, "previous_reasons": reasons, "mode": "ready",
                "target": None, "reply": reply, "reply_parts": parts, "reply_route": "respond"}

    def respond(state: WorkflowState) -> dict[str, Any]:
        options = [o.label for o in state.question.options] if state.question else []
        parts = state.reply_parts or ReplyParts(text=state.reply or "")  # any other reply is plain text
        message = Message(role="assistant", content=state.reply or "", options=options, parts=parts)
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
    return "\n\n".join([head, "\n".join(lines), NEXT_STEPS])


def workflow_summary_parts(state: WorkflowState) -> ReplyParts:
    lead = f'Your workflow "{state.workflow.name}" runs like this:' if state.workflow else "Here is what I have so far:"
    return ReplyParts(lead=lead, plan=_plan_rows(state, with_values=True), text=NEXT_STEPS)


def _plan_rows(state: WorkflowState, with_values: bool = False) -> list[PlanRow]:
    """The plan as rows for the UI: role, label and the condition outcome the step runs on."""
    roles = {"trigger": "When", "condition": "If", "action": "Then"}
    parents = links(state.steps)
    rows = []
    for step in state.steps:
        branch = next((b for _, b in parents.get(step.id, []) if b), None)
        if step.kind == "condition":  # "New rows found", not "If: New rows found"
            label = step.operation if step.operation.lower() not in ("if", "condition") else step.title
        else:
            label = step.title if step.app else f"{step.operation} (app to choose)"
        values = ", ".join(f"{p.label}: {display(p, p.value)}" for p in step.params if p.value is not None)
        if with_values and values:
            label += f" ({values})"
        rows.append(PlanRow(role=roles[step.kind], label=label, branch={"true": "yes", "false": "no"}.get(branch)))
    return rows


def _saved(state: WorkflowState) -> str | None:
    return "; ".join(state.changes) or None


def _structure(state: WorkflowState) -> list[tuple[str, str | None]]:
    return [(s.id, s.app) for s in state.steps]


def _plan_text(state: WorkflowState) -> str:
    roles = {"trigger": "When", "condition": "If", "action": "Then", "true": "If yes", "false": "Otherwise"}
    steps, parents = state.steps, links(state.steps)
    titles = {s.id: s.title for s in steps}
    lines = []
    for i, step in enumerate(steps, 1):
        follows = parents.get(step.id, [])
        branch = next((b for _, b in follows if b), None)
        line = f"{i}. **{roles[branch or step.kind]}:** {_step_label(step)}"
        if i > 2 and [p for p, _ in follows] != [steps[i - 2].id]:  # not simply after the step above it
            line += f" _(after {', '.join(titles.get(p, p) for p, _ in follows)})_"
        lines.append(line)
    return "Here's the plan:\n\n" + "\n".join(lines)


def _step_label(step) -> str:
    return step.title if step.app else f"{step.operation} _(app to choose)_"


def run_turn(graph, state: WorkflowState, user_message: str) -> WorkflowState:
    result = graph.invoke(state.model_copy(update={"latest_user_message": " ".join(user_message.split())}))
    return WorkflowState.model_validate(result)
