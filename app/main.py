from functools import lru_cache
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.core.config import ConfigError, load_settings
from app.pipeline.graph import build_graph, run_turn
from app.core.llm import build_llm
from app.pipeline.planning import build_plan
from app.state.models import FieldValue, Message, Mode, WorkflowState
from app.state.store import SessionStore
from app.pipeline.validation.readiness import evaluate

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


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    mode: Mode
    all_collected: bool
    asking_field: str | None
    fields: list[FieldView]
    workflow: dict[str, Any] | None


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
    readiness = evaluate(state, build_plan(state.values(), state.intent.cardinality))
    fields = [
        FieldView(key=r.key, node=r.node, parameter=r.label, value=r.value, status=r.status,
                  note=r.note, required=r.required)
        for r in readiness.rows
        if r.status != "not_applicable"
    ]
    return {
        "session_id": session_id,
        "reply": reply,
        "mode": state.mode,
        "all_collected": readiness.ready,
        "asking_field": state.target_field,
        "fields": fields,
        "workflow": state.workflow.model_dump(by_alias=True) if state.workflow else None,
    }


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
