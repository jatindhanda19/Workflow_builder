"""The HTTP API. Every session endpoint needs a signed-in user (Bearer token from /auth/login), and a session is
only ever visible to the user who created it."""

import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from functools import lru_cache
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.types import Message as ASGIMessage

from app.core.config import ConfigError, cors_origins, load_settings, log_level
from app.core.llm import build_llm
from app.core.logging import configure_logging, request_id
from app.core.turnlog import log_turn
from app.pipeline.diagram.mermaid import to_mermaid
from app.pipeline.graph import build_graph, run_turn
from app.pipeline.planner_schema import ParamKind, StepKind
from app.pipeline.validation import display, is_ready, rows
from app.runtime import RunError, run_workflow
from app.security.auth import (
    AuthError,
    authenticate_user,
    create_access_token,
    get_current_user,
    init_auth_db,
    register_user,
    validate_secret,
)
from app.state.machine import new_workflow, previous_reasons
from app.state.models import FieldValue, Message, Mode, PreviousReason, ReplyParts, WorkflowState
from app.state.store import SessionStore

configure_logging(log_level())
logger = logging.getLogger(__name__)

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_BODY_BYTES = 1024 * 1024  # a chat message is at most 4000 characters; run input is a small JSON object
NOT_FOUND = "session not found"
CONFLICT = "This session was changed by another request at the same time. Reload it and try again."


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail at startup, not on the first request, when auth is misconfigured.
    validate_secret()
    init_auth_db()
    yield


app = FastAPI(title="Flowwright", lifespan=lifespan)
store = SessionStore()


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class UserView(BaseModel):
    id: str
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserView


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)
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


class SessionSummaryView(BaseModel):
    id: str
    title: str  # the workflow's name, or the start of the first message
    mode: Mode
    generated: bool  # a workflow has been generated in this session
    updated_at: str


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


CurrentUser = Annotated[dict, Depends(get_current_user)]
SessionId = Annotated[str, Path(pattern=SESSION_ID_PATTERN)]


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


def _owned_state(session_id: str, user: dict) -> WorkflowState:
    """The user's session; 404 if it does not exist or belongs to someone else (the two look the same)."""
    state = store.get(session_id, owner_id=user["id"])
    if state is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return state


# -- health ---------------------------------------------------------------------------------------------------
@app.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    """Readiness: the session database answers and the LLM is configured."""
    try:
        store.ping()
        load_settings()
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        logger.warning("not ready: %s", exc)
        raise HTTPException(status_code=503, detail="not ready") from exc
    return {"status": "ready"}


# -- auth -----------------------------------------------------------------------------------------------------
@app.post("/auth/register", response_model=UserView, status_code=status.HTTP_201_CREATED)
def register(credentials: Credentials) -> dict:
    try:
        return register_user(credentials.email, credentials.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/auth/login", response_model=TokenResponse)
def login(credentials: Credentials) -> dict:
    user = authenticate_user(credentials.email, credentials.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Incorrect email or password.",
                            headers={"WWW-Authenticate": "Bearer"})
    return {"access_token": create_access_token(user["id"]), "user": user}


@app.get("/auth/me", response_model=UserView)
def me(user: CurrentUser) -> dict:
    return user


# -- sessions -------------------------------------------------------------------------------------------------
@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, user: CurrentUser, graph=Depends(get_graph)) -> dict:
    session_id = request.session_id or uuid4().hex
    stored = store.load(session_id, owner_id=user["id"])
    if stored is None and store.exists(session_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)  # someone else's session: never touch it
    before = (stored.state if stored else None) or WorkflowState()
    started = time.perf_counter()
    state = run_turn(graph, before, request.message)
    if not store.save(session_id, state, owner_id=user["id"], expected_version=stored.version if stored else 0):
        raise HTTPException(status_code=409, detail=CONFLICT)
    log_turn(session_id, before, state, round((time.perf_counter() - started) * 1000))
    return _summary(session_id, state)


@app.get("/sessions", response_model=list[SessionSummaryView])
def list_sessions(user: CurrentUser, limit: Annotated[int, Query(ge=1, le=200)] = 100) -> list[dict]:
    """The signed-in user's sessions, most recently changed first."""
    return [asdict(s) for s in store.list_sessions(user["id"], limit=limit)]


@app.get("/sessions/{session_id}", response_model=SessionView)
def get_session(session_id: SessionId, user: CurrentUser) -> dict:
    state = _owned_state(session_id, user)
    return {**_summary(session_id, state), "messages": _messages(state)}


@app.post("/sessions/{session_id}/new", response_model=SessionView)
def new_session_workflow(session_id: SessionId, user: CurrentUser) -> dict:
    """Starts a new workflow in the same session, without the LLM: a generated workflow is kept under previous
    workflows; the unfinished draft and the chat are cleared."""
    stored = store.load(session_id, owner_id=user["id"])
    if stored is None or stored.state is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    state = new_workflow(stored.state)
    if not store.save(session_id, state, owner_id=user["id"], expected_version=stored.version):
        raise HTTPException(status_code=409, detail=CONFLICT)
    return {**_summary(session_id, state), "messages": _messages(state)}


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: SessionId, user: CurrentUser) -> None:
    if not store.delete(session_id, owner_id=user["id"]):
        raise HTTPException(status_code=404, detail=NOT_FOUND)


@app.post("/sessions/{session_id}/run", response_model=RunResponse)
def run(session_id: SessionId, request: RunRequest, user: CurrentUser) -> dict:
    """Runs the session's generated workflow for real (see app/runtime/executor.py)."""
    state = _owned_state(session_id, user)
    if state.workflow is None:
        raise HTTPException(status_code=409, detail="no workflow has been generated yet")
    try:
        results = run_workflow(state.workflow, request.input)
    except RunError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"steps": [asdict(r) for r in results]}


# -- middleware -----------------------------------------------------------------------------------------------
class _BodyTooLarge(HTTPException):
    # An HTTPException, so FastAPI passes it on as 413 when it is raised while the endpoint reads the body.
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body is too large.")


class RequestContextMiddleware:
    """For every HTTP request:
    - gives it an id (the caller's X-Request-ID if it is safe, else a new one), returned in the X-Request-ID
      header and added to every log line;
    - refuses a body over `max_body_bytes` with 413;
    - turns an unhandled error into a generic 500 that carries the request id, with the stack trace logged.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int = MAX_BODY_BYTES) -> None:
        self.app, self.max_body_bytes = app, max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if REQUEST_ID.fullmatch(incoming) else uuid4().hex[:16]
        token = request_id.set(rid)
        started, received = False, 0

        async def send_with_id(message: ASGIMessage) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
            await send(message)

        async def limited_receive() -> ASGIMessage:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _BodyTooLarge
            return message

        try:
            length = headers.get(b"content-length", b"0")
            if not length.isdigit() or int(length) > self.max_body_bytes:
                raise _BodyTooLarge
            await self.app(scope, limited_receive, send_with_id)
        except _BodyTooLarge:
            if started:
                raise
            await JSONResponse(status_code=413, content={"detail": _BodyTooLarge().detail})(scope, receive, send_with_id)
        except Exception:
            logger.exception("unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if started:
                raise
            await JSONResponse(status_code=500, content={"detail": "Internal server error.", "request_id": rid})(
                scope, receive, send_with_id)
        finally:
            request_id.reset(token)


# The last middleware added runs first: CORS wraps everything, so even a 413 or 500 reaches the browser.
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
