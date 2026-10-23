"""Authentication utilities for Flowwright."""

import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

TOKEN_LIFETIME_MINUTES = 30
TOKEN_ISSUER = "workflow-builder"
ALGORITHM = "HS256"

password_hasher = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)


class AuthError(Exception):
    """Expected registration or login error."""


def auth_db_path() -> Path:
    """Read at call time, so AUTH_DB from .env or a test applies."""
    return Path(os.getenv("AUTH_DB", "auth.db"))


@lru_cache
def _dummy_hash() -> str:
    """Verified against when the email is unknown, so a login takes as long whether or not the account exists."""
    return password_hasher.hash(uuid4().hex)


def _secret() -> str:
    """Load the signing key and fail closed if it is missing or weak."""

    value = os.getenv("AUTH_SECRET_KEY", "")

    if len(value) < 32:
        raise RuntimeError(
            "AUTH_SECRET_KEY is missing or too short. Configure it securely."
        )

    return value


def validate_secret() -> None:
    """Called at startup, so a missing or weak key stops the server instead of failing every login."""
    _secret()


_initialized: set[Path] = set()  # databases whose users table exists


def _connect() -> sqlite3.Connection:
    path = auth_db_path()
    if path not in _initialized:
        init_auth_db()
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init_auth_db() -> None:
    """Create the users table if it does not exist."""

    path = auth_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    if path.exists():
        os.chmod(path, 0o600)

    _initialized.add(path)


def register_user(email: str, password: str) -> dict:
    """Create an account and return public account information."""

    normalized_email = email.strip().lower()

    if (
        len(normalized_email) > 254
        or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
            r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
            normalized_email,
        )
    ):
        raise AuthError("Enter a valid email address.")

    if len(password) < 12:
        raise AuthError("Password must contain at least 12 characters.")

    if len(password) > 128:
        raise AuthError("Password must not exceed 128 characters.")

    user_id = uuid4().hex
    password_hash = password_hasher.hash(password)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with closing(_connect()) as db, db:
            db.execute(
                """
                INSERT INTO users (id, email, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, normalized_email, password_hash, created_at),
            )
    except sqlite3.IntegrityError as exc:
        raise AuthError("An account with this email already exists.") from exc

    return {"id": user_id, "email": normalized_email}


def authenticate_user(email: str, password: str) -> dict | None:
    """Verify credentials without returning password hashes."""

    normalized_email = email.strip().lower()

    with closing(_connect()) as db:
        row = db.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (normalized_email,),
        ).fetchone()

    try:
        valid = password_hasher.verify(password, row["password_hash"] if row else _dummy_hash())
    except (ValueError, TypeError):
        return None

    if not valid or row is None:
        return None

    return {"id": row["id"], "email": row["email"]}


def create_access_token(user_id: str) -> str:
    """Create a signed access token that expires after 30 minutes."""

    now = datetime.now(timezone.utc)

    payload = {
        "sub": user_id,
        "type": "access",
        "iss": TOKEN_ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_LIFETIME_MINUTES),
    }

    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Require a valid Bearer token and return the authenticated user."""

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required or token invalid/expired.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        payload = jwt.decode(
            credentials.credentials,
            _secret(),
            algorithms=[ALGORITHM],
            issuer=TOKEN_ISSUER,
            options={"require": ["sub", "exp", "iat", "iss", "type"]},
        )
    except (jwt.InvalidTokenError, RuntimeError):
        raise unauthorized from None

    if payload.get("type") != "access":
        raise unauthorized

    user_id = payload.get("sub")

    if not isinstance(user_id, str) or not user_id:
        raise unauthorized

    with closing(_connect()) as db:
        row = db.execute(
            "SELECT id, email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if row is None:
        raise unauthorized

    return {"id": row["id"], "email": row["email"]}
