"""Application logging: one format for every logger, with the request id of the request being handled.

    2026-10-23 19:30:18,123 WARNING app.core.llm.client [3f2a9c1e] llm planner: openai/gpt-oss-120b is rate limited

The per-turn JSON lines of app.core.turnlog keep their own handler, so they stay one JSON record per line.
"""

import logging
import sys
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="-")
FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Sends app logs to stderr at `level`. Safe to call more than once."""
    logger = logging.getLogger("app")
    logger.setLevel(level)
    if any(getattr(h, "_app_handler", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(_RequestIdFilter())
    handler._app_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False
