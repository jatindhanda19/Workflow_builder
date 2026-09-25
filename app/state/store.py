from threading import Lock

from app.state.models import WorkflowState

class SessionStore:
    def __init__(self) -> None:
        self._states: dict[str, WorkflowState] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> WorkflowState | None:
        with self._lock:
            return self._states.get(session_id)

    def save(self, session_id: str, state: WorkflowState) -> None:
        with self._lock:
            self._states[session_id] = state

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._states.pop(session_id, None) is not None
