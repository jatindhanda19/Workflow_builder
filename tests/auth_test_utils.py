from uuid import uuid4


def authenticate_test_client(client):
    """Register a unique test user and authenticate the test client."""
    email = f"test-{uuid4().hex}@example.com"
    password = "Test-Password-123!"

    registration = client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert registration.status_code in (200, 201), registration.text

    login = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text

    token = login.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})

    user_response = client.get("/auth/me")
    assert user_response.status_code == 200, user_response.text

    return user_response.json()
