"""Append-only audit trail (D-43): one helper so every event has the same shape. Rows are never updated."""
from typing import Any

import psycopg

from app.db import to_jsonb


def record_event(conn: psycopg.Connection, tenant_id: str, entity_type: str, entity_id: Any, event_type: str,
                 actor: str, *, from_status: str | None = None, to_status: str | None = None,
                 data: dict | None = None, request_hash: str | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_events (tenant_id, entity_type, entity_id, event_type, actor, from_status, to_status,"
        " data, request_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (tenant_id, entity_type, str(entity_id), event_type, actor, from_status, to_status,
         to_jsonb(data or {}), request_hash),
    )


def history(conn: psycopg.Connection, tenant_id: str, entity_type: str, entity_id: Any) -> list[dict]:
    return conn.execute(
        "SELECT event_id, event_type, actor, from_status, to_status, data, occurred_at FROM audit_events"
        " WHERE tenant_id = %s AND entity_type = %s AND entity_id = %s ORDER BY event_id",
        (tenant_id, entity_type, str(entity_id)),
    ).fetchall()
