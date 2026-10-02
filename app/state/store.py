"""Sessions in SQLite: one row per session, holding its whole state as JSON, so a conversation survives a
backend restart and several backend processes can share it. Python's built-in sqlite3, no extra service."""

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from app.state.models import WorkflowState

logger = logging.getLogger(__name__)
DEFAULT_DB = Path(os.getenv("SESSION_DB", "sessions.db"))
SCHEMA = """CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""


class SessionStore:
    def __init__(self, path: Path | str = DEFAULT_DB) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(SCHEMA)

    def get(self, session_id: str) -> WorkflowState | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        try:
            return WorkflowState.model_validate_json(row[0])
        except ValidationError:
            logger.exception("could not read session %s; starting it fresh", session_id)
            return None

    def save(self, session_id: str, state: WorkflowState) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as db, db:  # the inner `db` commits the transaction
            db.execute(
                "INSERT INTO sessions (id, state, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                (session_id, state.model_dump_json(), now),
            )

    def delete(self, session_id: str) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        # One short connection per call: safe across FastAPI's worker threads without a shared lock.
        return sqlite3.connect(self._path, timeout=10)
