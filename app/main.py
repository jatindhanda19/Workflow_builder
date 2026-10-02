import os
import time
from dataclasses import asdict
from functools import lru_cache
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.core.config import ConfigError, load_settings
from app.core.llm import build_llm
from app.core.turnlog import log_turn
from app.pipeline.diagram.mermaid import to_mermaid
from app.pipeline.graph import build_graph, run_turn
from app.pipeline.planner_schema import ParamKind, StepKind
from app.pipeline.validation import display, is_ready, rows
from app.runtime import RunError, run_workflow
from app.state.machine import new_workflow, previous_reasons
from app.state.models import FieldValue, Message, Mode, PreviousReason, ReplyParts, WorkflowState
from app.state.store import SessionStore

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"

app = FastAPI(title="Workflow Builder")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",") if o.strip()],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
store = SessionStore()


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class FieldView(BaseModel):
    key: str
    node: str
    parameter: str
    value: FieldValue | None
    status: str
    note: str | None
    required: bool
    display: str  # the value as shown to the user: "18:00" is "6:00 PM", a list is "a, b"


class ParamView(BaseModel):
    key: str  # "<step id>.<name>"
    name: str
    label: str
    kind: ParamKind
    value: FieldValue | None
    display: str
    status: str  # filled | missing | ambiguous
    note: str | None
    required: bool
    choices: list[str]


class StepView(BaseModel):
    id: str
    kind: StepKind
    app: str | None
    operation: str
    title: str
    params: list[ParamView]


class QuestionView(BaseModel):
    """The current multiple-choice question. Sending an option's label (or its letter) answers it."""

    target: str | None
    text: str
    options: list[str]
    allow_custom: bool
    step: str | None  # "Step 1 of 4 · Trigger: Schedule: Every weekday"
    note: str | None  # why the last answer was rejected, or that this was asked before


class WorkflowView(BaseModel):
    workflow: dict[str, Any]
    diagram: str
    reason: PreviousReason  # edited: an older version of a workflow; new_workflow: replaced by a new request


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    mode: Mode
    all_collected: bool
    asking_field: str | None
    question: QuestionView | None
    fields: list[FieldView]
    workflow: dict[str, Any] | None
    diagram: str | None
    previous_workflows: list[WorkflowView]  # generated earlier in this session, oldest first
    name: str | None
    steps: list[StepView]
    parts: ReplyParts  # the last reply in pieces; `reply` is the same as Markdown


class SessionView(ChatResponse):
    messages: list[Message]


class RunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)  # the trigger's data, e.g. a webhook body


class StepRunView(BaseModel):
    id: str
    name: str
    status: str  # ok | failed | skipped
    output: Any = None
    error: str | None = None


class RunResponse(BaseModel):
    steps: list[StepRunView]


@lru_cache
def _compiled_graph():
    return build_graph(build_llm(load_settings()))


def get_graph():
    try:
        return _compiled_graph()
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _summary(session_id: str, state: WorkflowState) -> dict:
    last = next((m for m in reversed(state.messages) if m.role == "assistant"), None)
    reply = last.content if last else ""
    params = {f"{s.id}.{p.name}": p for s in state.steps for p in s.params}  # an app row has no Param
    fields = [
        FieldView(key=r.key, node=r.step, parameter=r.label, value=r.value, status=r.status,
                  note=r.note, required=r.required, display=display(params.get(r.key), r.value))
        for r in rows(state)
    ]
    workflow = state.workflow.model_dump(by_alias=True) if state.workflow else None
    previous = zip(state.previous_workflows, previous_reasons(state))
    return {
        "session_id": session_id,
        "reply": reply,
        "mode": state.mode,
        "all_collected": is_ready(state),
        "asking_field": state.target,
        "question": _question_view(state),
        "fields": fields,
        "workflow": workflow,
        "diagram": to_mermaid(workflow) if workflow else None,
        "previous_workflows": [_workflow_view(w.model_dump(by_alias=True), reason) for w, reason in previous],
        "name": state.name or (state.workflow.metadata.name if state.workflow else None),
        "steps": _steps(state),
        "parts": _parts(last) if last else ReplyParts(),
    }


def _workflow_view(workflow: dict, reason: PreviousReason) -> WorkflowView:
    return WorkflowView(workflow=workflow, diagram=to_mermaid(workflow), reason=reason)


def _steps(state: WorkflowState) -> list[StepView]:
    """The draft's steps with their parameters; same filter and status as the `fields` rows."""
    views = []
    for step in state.steps:
        params = [
            ParamView(key=f"{step.id}.{p.name}", name=p.name, label=p.label, kind=p.kind, value=p.value,
                      display=display(p, p.value), note=p.note, required=p.required, choices=p.choices,
                      status="filled" if p.value is not None else ("ambiguous" if p.note else "missing"))
            for p in step.params if p.value is not None or p.required
        ]
        views.append(StepView(id=step.id, kind=step.kind, app=step.app, operation=step.operation,
                              title=step.title, params=params))
    return views


def _parts(message: Message) -> ReplyParts:
    # Messages saved before replies had parts are shown as plain text.
    return message.parts or ReplyParts(text=message.content)


def _messages(state: WorkflowState) -> list[Message]:
    return [m.model_copy(update={"parts": _parts(m)}) if m.role == "assistant" else m for m in state.messages]


def _question_view(state: WorkflowState) -> QuestionView | None:
    question = state.question
    if question is None:
        return None
    return QuestionView(target=question.target, text=question.text, options=[o.label for o in question.options],
                        allow_custom=question.allow_custom, step=question.step, note=question.note)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, graph=Depends(get_graph)) -> dict:
    session_id = request.session_id or uuid4().hex
    before = store.get(session_id) or WorkflowState()
    started = time.perf_counter()
    state = run_turn(graph, before, request.message)
    log_turn(session_id, before, state, round((time.perf_counter() - started) * 1000))
    store.save(session_id, state)
    return _summary(session_id, state)


@app.get("/sessions/{session_id}", response_model=SessionView)
def get_session(session_id: str) -> dict:
    state = store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {**_summary(session_id, state), "messages": _messages(state)}


@app.post("/sessions/{session_id}/new", response_model=SessionView)
def new_session_workflow(session_id: str) -> dict:
    """Starts a new workflow in the same session, without the LLM: a generated workflow is kept under previous
    workflows; the unfinished draft and the chat are cleared."""
    state = store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    state = new_workflow(state)
    store.save(session_id, state)
    return {**_summary(session_id, state), "messages": _messages(state)}


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    if not store.delete(session_id):
        raise HTTPException(status_code=404, detail="session not found")


@app.post("/sessions/{session_id}/run", response_model=RunResponse)
def run(session_id: str, request: RunRequest) -> dict:
    """Runs the session's generated workflow for real (see app/runtime/executor.py)."""
    state = store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    if state.workflow is None:
        raise HTTPException(status_code=409, detail="no workflow has been generated yet")
    try:
        results = run_workflow(state.workflow, request.input)
    except RunError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"steps": [asdict(r) for r in results]}
