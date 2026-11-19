"""Read and delete execution logs. No event update operation is provided."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing

from backend.app.db.database import connect_db


class LogSessionNotFound(LookupError):
    pass


class LogPayloadError(ValueError):
    pass


def _reject_non_json_number(value: str):
    raise ValueError("non-JSON number")


class LogService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection] = connect_db):
        self.connection_factory = connection_factory

    def list_sessions(self, *, limit: int, offset: int) -> dict:
        with closing(self.connection_factory()) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            total = db.execute("SELECT count(DISTINCT session_id) FROM execution_event").fetchone()[0]
            rows = db.execute(
                """
                SELECT summary.session_id, summary.event_count, summary.run_count,
                       first.created_at AS first_event_at, last.created_at AS last_event_at,
                       summary.error_count
                FROM (
                    SELECT session_id, count(*) AS event_count,
                           count(DISTINCT run_id) AS run_count,
                           min(id) AS first_id, max(id) AS last_id,
                           sum(CASE WHEN event_type = 'error' THEN 1 ELSE 0 END) AS error_count
                    FROM execution_event
                    GROUP BY session_id
                ) AS summary
                JOIN execution_event AS first ON first.id = summary.first_id
                JOIN execution_event AS last ON last.id = summary.last_id
                ORDER BY summary.last_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {"total": total, "limit": limit, "offset": offset, "sessions": [dict(row) for row in rows]}

    def get_session(self, session_id: str, *, limit: int, offset: int) -> dict:
        with closing(self.connection_factory()) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            total = db.execute(
                "SELECT count(*) FROM execution_event WHERE session_id = ?", (session_id,),
            ).fetchone()[0]
            if total == 0:
                raise LogSessionNotFound()
            rows = db.execute(
                """
                SELECT id, session_id, run_id, event_type, created_at, duration_ms, payload
                FROM execution_event WHERE session_id = ?
                ORDER BY id ASC LIMIT ? OFFSET ?
                """,
                (session_id, limit, offset),
            ).fetchall()

        events = []
        for row in rows:
            event = dict(row)
            if event["payload"] is not None:
                try:
                    payload = json.loads(event["payload"], parse_constant=_reject_non_json_number)
                    if not isinstance(payload, dict):
                        raise ValueError("event payload must be an object")
                except (TypeError, ValueError, RecursionError):
                    raise LogPayloadError() from None
                event["payload"] = payload
            events.append(event)
        return {"session_id": session_id, "total": total, "limit": limit, "offset": offset, "events": events}

    def delete_session(self, session_id: str) -> dict:
        with closing(self.connection_factory()) as db, db:
            deleted = db.execute(
                "DELETE FROM execution_event WHERE session_id = ?", (session_id,),
            ).rowcount
            if deleted == 0:
                raise LogSessionNotFound()
        return {"session_id": session_id, "deleted_count": deleted}
