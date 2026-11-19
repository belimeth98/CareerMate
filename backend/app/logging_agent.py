"""Store received events unchanged. No model calls or event summarisation."""

import sqlite3
from collections.abc import Callable
from contextlib import closing

from backend.app.db.database import connect_db
from backend.app.execution_events import ExecutionEvent


class LoggingAgent:
    def __init__(
        self, connection_factory: Callable[[], sqlite3.Connection] = connect_db,
    ) -> None:
        self.connection_factory = connection_factory
        self.stored_count = 0

    def __call__(self, event: ExecutionEvent) -> None:
        # Let EventBus isolate failures; do not publish another event on failure.
        with closing(self.connection_factory()) as connection:
            connection.execute("PRAGMA busy_timeout = 100")
            with connection:
                connection.execute(
                    """
                    INSERT INTO execution_event (
                        session_id, run_id, event_type, created_at, duration_ms, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.session_id, event.run_id, event.event_type,
                        event.created_at, event.duration_ms, event.payload,
                    ),
                )
        self.stored_count += 1
