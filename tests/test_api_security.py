"""The API's production guards: sign-in, sessions scoped to their owner, input limits, request ids, a generic
500 for unexpected errors, readiness, and a startup check of the signing key. The LLM is scripted."""

import pytest
from auth_test_utils import authenticate_test_client
from fastapi.testclient import TestClient
from test_clarification import ScriptedLLM, _plan

import app.main as main
from app.pipeline.graph import build_graph
from app.state.models import WorkflowState
from app.state.store import SessionStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "sessions.db")
    monkeypatch.setattr(main, "store", store)
    return store


@pytest.fixture
def scripted(monkeypatch):
    def use(*plans):
        graph = build_graph(ScriptedLLM(*plans))
        main.app.dependency_overrides[main.get_graph] = lambda: graph
    yield use
    main.app.dependency_overrides.clear()


def test_session_endpoints_need_a_signed_in_user(store):
    client = TestClient(main.app)
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/sessions/abc").status_code == 401
    assert client.post("/sessions/abc/run", json={}).status_code == 401
    assert client.delete("/sessions/abc").status_code == 401


def test_chat_never_overwrites_another_users_session(store, scripted):
    scripted(_plan())
    owner, intruder = TestClient(main.app), TestClient(main.app)
    owner_id = authenticate_test_client(owner)["id"]
    authenticate_test_client(intruder)
    store.save("shared-id", WorkflowState(name="Owner's workflow"), owner_id=owner_id)

    response = intruder.post("/chat", json={"session_id": "shared-id", "message": "Delete everything"})

    assert response.status_code == 404
    assert store.get("shared-id", owner_id=owner_id).name == "Owner's workflow"


def test_a_new_session_belongs_to_its_creator(store, scripted):
    scripted(_plan())
    owner, other = TestClient(main.app), TestClient(main.app)
    authenticate_test_client(owner)
    authenticate_test_client(other)
    session = owner.post("/chat", json={"message": "When a form is submitted add it to Airtable"}).json()["session_id"]
    assert owner.get(f"/sessions/{session}").status_code == 200
    assert other.get(f"/sessions/{session}").status_code == 404


def test_concurrent_change_is_refused_not_lost(store, scripted, monkeypatch):
    scripted(_plan())
    client = TestClient(main.app)
    owner_id = authenticate_test_client(client)["id"]
    store.save("s1", WorkflowState(), owner_id=owner_id)
    real_run_turn = main.run_turn

    def run_turn_while_another_request_saves(graph, state, message):
        store.save("s1", WorkflowState(name="Saved by the other request"), owner_id=owner_id)
        return real_run_turn(graph, state, message)

    monkeypatch.setattr(main, "run_turn", run_turn_while_another_request_saves)
    response = client.post("/chat", json={"session_id": "s1", "message": "hello"})
    assert response.status_code == 409
    assert store.get("s1", owner_id=owner_id).name == "Saved by the other request"


@pytest.mark.parametrize("session_id", ["../etc/passwd", "x" * 65, "has space"])
def test_unsafe_session_ids_are_rejected(store, session_id):
    client = TestClient(main.app)
    authenticate_test_client(client)
    assert client.post("/chat", json={"session_id": session_id, "message": "hi"}).status_code == 422
    assert client.get(f"/sessions/{session_id}").status_code in (404, 422)


def test_oversized_body_is_refused(store):
    client = TestClient(main.app)
    response = client.post("/auth/login", content=b"x" * (main.MAX_BODY_BYTES + 1),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    chunked = (b"x" * 65536 for _ in range(main.MAX_BODY_BYTES // 65536 + 2))  # no Content-Length header
    response = client.post("/auth/login", content=chunked, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_every_response_carries_a_request_id():
    client = TestClient(main.app)
    assert len(client.get("/health").headers["x-request-id"]) == 16
    assert client.get("/health", headers={"X-Request-ID": "trace-123"}).headers["x-request-id"] == "trace-123"
    assert client.get("/health", headers={"X-Request-ID": "bad id\n"}).headers["x-request-id"] != "bad id\n"


def test_unexpected_error_is_a_generic_500_with_the_request_id(store, monkeypatch):
    def broken_graph():
        raise RuntimeError("secret internal detail")

    main.app.dependency_overrides[main.get_graph] = broken_graph
    try:
        client = TestClient(main.app)
        authenticate_test_client(client)
        response = client.post("/chat", json={"message": "hi"}, headers={"X-Request-ID": "req-1"})
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error.", "request_id": "req-1"}
    assert "secret internal detail" not in response.text


def test_readiness_reports_a_missing_llm_key(store, monkeypatch):
    client = TestClient(main.app)
    monkeypatch.setattr(main, "load_settings", lambda: None)
    assert client.get("/ready").json() == {"status": "ready"}

    def no_key():
        raise main.ConfigError("GROQ_API_KEY is not set")

    monkeypatch.setattr(main, "load_settings", no_key)
    assert client.get("/ready").status_code == 503


def test_login_with_an_unknown_email_is_rejected():
    response = TestClient(main.app).post("/auth/login", json={"email": "nobody@example.com",
                                                              "password": "Whatever-Password-1"})
    assert response.status_code == 401


def test_cors_preflight_allows_the_authorization_header():
    response = TestClient(main.app).options("/chat", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type"})
    assert response.status_code == 200
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_startup_fails_without_a_strong_signing_key(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET_KEY", "too-short")
    with pytest.raises(RuntimeError, match="AUTH_SECRET_KEY"):
        with TestClient(main.app):
            pass
