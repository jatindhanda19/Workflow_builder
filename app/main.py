from functools import lru_cache
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.core.config import ConfigError, load_settings
from app.core.llm import build_llm
from app.pipeline.diagram.mermaid import to_mermaid
from app.pipeline.graph import build_graph, run_turn
from app.pipeline.validation import is_ready, rows
from app.state.models import FieldValue, Message, Mode, WorkflowState
from app.state.store import SessionStore

app = FastAPI(title="Workflow Builder")
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


class QuestionView(BaseModel):
    """The current multiple-choice question. Sending an option's label (or its letter) answers it."""

    target: str | None
    text: str
    options: list[str]
    allow_custom: bool


class WorkflowView(BaseModel):
    workflow: dict[str, Any]
    diagram: str


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


class SessionView(ChatResponse):
    messages: list[Message]


@lru_cache
def _compiled_graph():
    return build_graph(build_llm(load_settings()))


def get_graph():
    try:
        return _compiled_graph()
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _summary(session_id: str, state: WorkflowState) -> dict:
    reply = next((m.content for m in reversed(state.messages) if m.role == "assistant"), "")
    fields = [
        FieldView(key=r.key, node=r.step, parameter=r.label, value=r.value, status=r.status,
                  note=r.note, required=r.required)
        for r in rows(state)
    ]
    workflow = state.workflow.model_dump(by_alias=True) if state.workflow else None
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
        "previous_workflows": [_workflow_view(w.model_dump(by_alias=True)) for w in state.previous_workflows],
    }


def _workflow_view(workflow: dict) -> WorkflowView:
    return WorkflowView(workflow=workflow, diagram=to_mermaid(workflow))


def _question_view(state: WorkflowState) -> QuestionView | None:
    question = state.question
    if question is None:
        return None
    return QuestionView(target=question.target, text=question.text, options=[o.label for o in question.options],
                        allow_custom=question.allow_custom)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, graph=Depends(get_graph)) -> dict:
    session_id = request.session_id or uuid4().hex
    state = store.get(session_id) or WorkflowState()
    state = run_turn(graph, state, request.message)
    store.save(session_id, state)
    return _summary(session_id, state)


@app.get("/sessions/{session_id}", response_model=SessionView)
def get_session(session_id: str) -> dict:
    state = store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {**_summary(session_id, state), "messages": state.messages}


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    if not store.delete(session_id):
        raise HTTPException(status_code=404, detail="session not found")
