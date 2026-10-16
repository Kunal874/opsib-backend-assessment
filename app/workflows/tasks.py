"""Tasks: the work a person does about an exception (D-34). System-side lifecycle for now:
create + route, cancel when the case closes, reopen + re-route when it reopens. Every step goes through
the state machine and writes an audit event.
"""
import uuid

import psycopg

from app.audit import record_event
from app.config import Settings
from app.routing.eligibility import RoutingDecision
from app.workflows.state_machine import OPEN, TaskStatus, next_status

TASK_TYPE = "INVESTIGATE_RECEIPT_SHORTFALL"
DETECTION, ROUTER = "system:detection", "system:router"


def task_id_for(exception_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(exception_id, TASK_TYPE)  # one stable task ID per (exception, task type)


def create_task_for_exception(conn: psycopg.Connection, settings: Settings, finding: dict, region_id: str | None,
                              decision: RoutingDecision) -> uuid.UUID | None:
    """Create and route the investigation task. Returns its ID if *this call* created it, else None.

    Safe to call on every detection run: the insert is a no-op when the task exists, and only the
    transaction that actually inserted it routes it. No target on ON CONFLICT: the PK and
    UNIQUE (exception_id, task_type) guard the same identity (D-77).
    """
    task_id = task_id_for(finding["exception_id"])
    due_at = finding["evaluated_as_of"] + settings.task_sla
    created = conn.execute(
        "INSERT INTO tasks (task_id, tenant_id, exception_id, task_type, status, canonical_location_id, region_id,"
        " due_at) VALUES (%s, %s, %s, %s, 'CREATED', %s, %s, %s) ON CONFLICT DO NOTHING RETURNING task_id",
        (task_id, finding["tenant_id"], finding["exception_id"], TASK_TYPE, finding["canonical_location_id"],
         region_id, due_at),
    ).fetchone()
    if created is None:
        return None
    record_event(conn, finding["tenant_id"], "task", task_id, "TASK_CREATED", DETECTION, to_status="CREATED",
                 data={"exception_id": finding["exception_id"], "due_at": due_at})
    _route(conn, finding["tenant_id"], task_id, decision)
    return task_id


def cancel_open_tasks(conn: psycopg.Connection, tenant_id: str, exception_id: uuid.UUID, reason: str) -> int:
    """The case closed by data (e.g. a late receipt): stop anyone working on it. Assignee kept for history."""
    cancelled = 0
    for task in conn.execute("SELECT task_id, status FROM tasks WHERE tenant_id = %s AND exception_id = %s"
                             " ORDER BY task_id FOR UPDATE", (tenant_id, exception_id)).fetchall():
        if task["status"] not in OPEN:
            continue  # already done / awaiting verification: leave it for the verifier (D-30 edge)
        _transition(conn, tenant_id, task["task_id"], task["status"], "cancel", DETECTION, {"reason": reason})
        cancelled += 1
    return cancelled


def reopen_and_route(conn: psycopg.Connection, settings: Settings, finding: dict, region_id: str | None,
                     decision: RoutingDecision) -> None:
    """The case reopened: bring its cancelled task back and route it again, with a fresh due time."""
    task = conn.execute("SELECT task_id, status FROM tasks WHERE exception_id = %s AND task_type = %s FOR UPDATE",
                        (finding["exception_id"], TASK_TYPE)).fetchone()
    if task is None:
        create_task_for_exception(conn, settings, finding, region_id, decision)
        return
    if task["status"] != TaskStatus.CANCELLED:
        return  # still open, or already completed — nothing to bring back
    conn.execute("UPDATE tasks SET assignee_employee_id = NULL, due_at = %s WHERE task_id = %s",
                 (finding["evaluated_as_of"] + settings.task_sla, task["task_id"]))
    _transition(conn, finding["tenant_id"], task["task_id"], task["status"], "reopen", DETECTION,
                {"reason": "Shortfall present again after re-evaluation"})
    _route(conn, finding["tenant_id"], task["task_id"], decision)


def _route(conn: psycopg.Connection, tenant_id: str, task_id: uuid.UUID, decision: RoutingDecision) -> None:
    if decision.assignee_id is None:
        record_event(conn, tenant_id, "task", task_id, "ROUTING_NEEDS_MANUAL_ASSIGNMENT", ROUTER,
                     from_status="CREATED", to_status="CREATED", data=decision.to_json())
        return
    _transition(conn, tenant_id, task_id, TaskStatus.CREATED, "assign", ROUTER, decision.to_json(),
                assignee=decision.assignee_id)


def _transition(conn: psycopg.Connection, tenant_id: str, task_id: uuid.UUID, current: str, action: str,
                actor: str, data: dict, assignee: str | None = None) -> TaskStatus:
    new = next_status(current, action)
    conn.execute("UPDATE tasks SET status = %s, assignee_employee_id = COALESCE(%s, assignee_employee_id),"
                 " updated_at = now() WHERE task_id = %s", (new, assignee, task_id))
    record_event(conn, tenant_id, "task", task_id, action.upper(), actor, from_status=str(current),
                 to_status=new, data=data)
    return new
