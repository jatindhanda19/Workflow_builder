from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main
from app.state.models import WorkflowState
from app.state.store import SessionStore
from auth_test_utils import authenticate_test_client


def test_protected_endpoints_reject_missing_or_invalid_tokens():
    with TestClient(main.app) as client:
        response = client.get("/auth/me")
        assert response.status_code == 401

        client.headers["Authorization"] = "Bearer invalid-token"
        response = client.get("/auth/me")
        assert response.status_code == 401


def test_registration_and_login():
    email = f"auth-{uuid4().hex}@example.com"
    password = "Secure-Test-Password-123!"

    with TestClient(main.app) as client:
        registration = client.post(
            "/auth/register",
            json={"email": email, "password": password},
        )
        assert registration.status_code in (200, 201), registration.text

        # Passwords must not be returned in the registration response.
        assert password not in registration.text

        login = client.post(
            "/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200, login.text
        assert login.json().get("access_token")

        wrong_password = client.post(
            "/auth/login",
            json={"email": email, "password": "Wrong-Password-123!"},
        )
        assert wrong_password.status_code == 401


def test_duplicate_registration_is_rejected():
    email = f"duplicate-{uuid4().hex}@example.com"
    password = "Secure-Test-Password-123!"

    with TestClient(main.app) as client:
        first = client.post(
            "/auth/register",
            json={"email": email, "password": password},
        )
        assert first.status_code in (200, 201), first.text

        duplicate = client.post(
            "/auth/register",
            json={"email": email.upper(), "password": password},
        )
        assert duplicate.status_code == 400


def test_users_cannot_access_or_delete_each_others_sessions(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        main,
        "store",
        SessionStore(tmp_path / "auth_sessions.db"),
    )

    with TestClient(main.app) as client_a, TestClient(main.app) as client_b:
        user_a = authenticate_test_client(client_a)
        user_b = authenticate_test_client(client_b)

        session_id = f"private-{uuid4().hex}"

        saved = main.store.save(
            session_id,
            WorkflowState(),
            owner_id=user_a["id"],
        )
        assert saved is True

        # The owner can access their own session.
        own_session = client_a.get(f"/sessions/{session_id}")
        assert own_session.status_code == 200, own_session.text

        # Another authenticated user cannot read or modify it.
        assert client_b.get(
            f"/sessions/{session_id}"
        ).status_code == 404

        assert client_b.post(
            f"/sessions/{session_id}/new"
        ).status_code == 404

        assert client_b.post(
            f"/sessions/{session_id}/run",
            json={},
        ).status_code == 404

        assert client_b.delete(
            f"/sessions/{session_id}"
        ).status_code == 404

        # Failed cross-user requests must not delete the owner's session.
        still_exists = client_a.get(f"/sessions/{session_id}")
        assert still_exists.status_code == 200, still_exists.text

        assert user_a["id"] != user_b["id"]
