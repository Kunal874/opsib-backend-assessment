"""Tasks: the work a person does about an exception (D-34).

System side: create + route, cancel when the case closes, reopen + re-route when it reopens.
People side: perform_action() — the only way a client can change a task (D-38..D-41).
Every step goes through the state machine and writes an audit event.
"""
import hashlib
import json
import uuid

import psycopg

from app.audit import history, record_event
from app.config import Settings
from app.db import to_jsonb
from app.errors import AppError
from app.mapping.resolver import load_alias_index
from app.routing.eligibility import Candidate, RoutingDecision, RoutingPolicy, can_supervise, check_eligibility
from app.routing.service import load_candidates
from app.workflows.state_machine import OPEN, InvalidTransition, TaskStatus, next_status

TASK_TYPE = "INVESTIGATE_RECEIPT_SHORTFALL"
DETECTION, ROUTER, WORKFLOW = "system:detection", "system:router", "system:workflow"
# Neutral on purpose: none of these asserts supplier fault (D-39).
RESOLUTION_CODES = frozenset({"SHORTFALL_CONFIRMED", "RECEIPT_POSTING_ERROR", "LATE_DELIVERY_EXPECTED", "OTHER"})
WORKER_ACTIONS = frozenset({"acknowledge", "start", "block", "unblock", "complete"})   # assignee only
SUPERVISOR_ACTIONS = frozenset({"verify", "reject", "reassign"})                        # supervisor, not assignee

TASK_SELECT = """
SELECT t.task_id, t.exception_id, t.task_type, t.status, t.canonical_location_id, t.region_id,
       t.assignee_employee_id, t.due_at, t.resolution_code, t.evidence_ref, t.notes, t.created_at, t.updated_at,
       e.po_number, e.business_key, e.status AS exception_status, e.variance_qty::text AS variance_qty, e.uom
  FROM tasks t JOIN exceptions e USING (exception_id)
"""


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


# ------------------------------------------------------------------ people: perform_action

def perform_action(conn: psycopg.Connection, settings: Settings, task_id: uuid.UUID | str, action: str,
                   actor_id: str, payload: dict) -> dict:
    """Apply one client action to a task. Returns {"task": ..., "replayed": bool}.

    Retry safety (the Task C design question, D-41): the row is locked, so concurrent calls run one at a
    time. If the action is no longer allowed *because this exact request already succeeded* (same action,
    actor and payload as the latest human event), the call is a replay: 200 with the current task and
    nothing written. Same action with a different payload is 409 CONFLICTING_RETRY; anything else is
    409 INVALID_TRANSITION.
    """
    if action not in WORKER_ACTIONS | SUPERVISOR_ACTIONS:
        raise AppError(403, "FORBIDDEN", f"'{action}' is not a client action")
    tenant_id = settings.tenant_id
    request_hash = hashlib.sha256(json.dumps({"action": action, "actor": actor_id, "payload": payload},
                                             sort_keys=True, default=str).encode()).hexdigest()
    with conn.transaction():
        task = conn.execute("SELECT * FROM tasks WHERE tenant_id = %s AND task_id = %s FOR UPDATE",
                            (tenant_id, task_id)).fetchone()
        if task is None:
            raise AppError(404, "NOT_FOUND", f"Task {task_id} not found")
        try:
            new_status = next_status(task["status"], action)
        except InvalidTransition:
            _replay_or_raise(conn, tenant_id, task, action, request_hash)
            replayed = True
        else:
            replayed = False
            roster = load_candidates(conn, tenant_id, settings.evaluation_time, load_alias_index(conn, tenant_id))
            actor = _authorize(roster, task, action, actor_id)
            _apply(conn, settings, roster, task, actor, action, new_status, _validated(action, payload),
                   request_hash)
    return {"task": get_task(conn, tenant_id, task_id), "replayed": replayed}


def get_task(conn: psycopg.Connection, tenant_id: str, task_id: uuid.UUID | str) -> dict:
    row = conn.execute(TASK_SELECT + " WHERE t.tenant_id = %s AND t.task_id = %s", (tenant_id, task_id)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", f"Task {task_id} not found")
    task = _shape(row)
    task["history"] = history(conn, tenant_id, "task", task_id)
    return task


def list_tasks(conn: psycopg.Connection, tenant_id: str, assignee_id: str | None = None,
               location_id: str | None = None, status: str | None = None) -> list[dict]:
    rows = conn.execute(
        TASK_SELECT + " WHERE t.tenant_id = %(t)s"
        " AND (%(a)s::text IS NULL OR t.assignee_employee_id = %(a)s)"
        " AND (%(l)s::text IS NULL OR t.canonical_location_id = %(l)s)"
        " AND (%(s)s::text IS NULL OR t.status = %(s)s)"
        " ORDER BY t.due_at, t.task_id",
        {"t": tenant_id, "a": assignee_id, "l": location_id, "s": status}).fetchall()
    return [_shape(r) for r in rows]


def _shape(row: dict) -> dict:
    task = {k: v for k, v in row.items() if k not in ("business_key", "exception_status", "uom")}
    task["exception"] = {"exception_id": row["exception_id"], "business_key": row["business_key"],
                         "status": row["exception_status"], "variance_qty": row["variance_qty"], "uom": row["uom"]}
    return task


def _replay_or_raise(conn: psycopg.Connection, tenant_id: str, task: dict, action: str, request_hash: str) -> None:
    last = conn.execute(
        "SELECT event_type, request_hash FROM audit_events WHERE tenant_id = %s AND entity_type = 'task'"
        " AND entity_id = %s AND actor NOT LIKE 'system:%%' ORDER BY event_id DESC LIMIT 1",
        (tenant_id, str(task["task_id"]))).fetchone()
    if last and last["event_type"] == action.upper():
        if last["request_hash"] == request_hash:
            return  # this exact request already succeeded: a safe replay
        raise AppError(409, "CONFLICTING_RETRY",
                       f"'{action}' was already applied with different data; refusing to overwrite it")
    raise AppError(409, "INVALID_TRANSITION", f"Cannot '{action}' a task that is {task['status']}",
                   {"status": task["status"], "action": action})


def _authorize(roster: list[Candidate], task: dict, action: str, actor_id: str) -> Candidate:
    # X-Actor-Id stands in for real authentication (D-40): the actor must exist in HRMS and be ACTIVE.
    actor = next((c for c in roster if c.employee_id == actor_id), None)
    if actor is None or actor.employment_status != "ACTIVE":
        raise AppError(403, "FORBIDDEN", f"{actor_id} is not an active employee")
    if action in WORKER_ACTIONS and actor_id != task["assignee_employee_id"]:
        raise AppError(403, "FORBIDDEN", f"Only the assignee can {action} this task")
    if action in SUPERVISOR_ACTIONS:
        if not can_supervise(actor, task["canonical_location_id"], task["region_id"]):
            raise AppError(403, "FORBIDDEN", f"{actor_id} does not supervise {task['canonical_location_id']}")
        if actor_id == task["assignee_employee_id"]:
            raise AppError(403, "FORBIDDEN", "The assignee cannot verify, reject or reassign their own task")
    return actor


def _validated(action: str, payload: dict) -> dict:
    def required(field: str) -> str:
        value = (payload.get(field) or "").strip()
        if not value:
            raise AppError(422, "VALIDATION_ERROR", f"'{field}' is required for {action}")
        return value

    def optional(field: str) -> str | None:
        return (payload.get(field) or "").strip() or None

    if action == "complete":
        code = required("resolution_code")
        if code not in RESOLUTION_CODES:
            raise AppError(422, "VALIDATION_ERROR", f"Unknown resolution_code '{code}'",
                           {"allowed": sorted(RESOLUTION_CODES)})
        if code == "OTHER" and not optional("notes"):
            raise AppError(422, "VALIDATION_ERROR", "'notes' are required when resolution_code is OTHER")
        return {"resolution_code": code, "evidence_ref": required("evidence_ref"), "notes": optional("notes")}
    if action in ("block", "reject"):
        return {"reason": required("reason")}
    if action == "reassign":
        return {"new_assignee_id": required("new_assignee_id"), "reason": required("reason")}
    if action == "verify":
        return {"notes": optional("notes")}
    return {}


def _apply(conn: psycopg.Connection, settings: Settings, roster: list[Candidate], task: dict, actor: Candidate,
           action: str, new_status: TaskStatus, data: dict, request_hash: str) -> None:
    task_id, tenant_id = task["task_id"], settings.tenant_id
    assignee, fields = task["assignee_employee_id"], {}

    if action == "reassign":
        assignee = data["new_assignee_id"]
        data = {**data, "previous_assignee_id": task["assignee_employee_id"],
                "check": _reassign_check(settings, roster, task, assignee)}
    elif action == "complete":
        fields = dict(data)  # resolution_code, evidence_ref, notes
    elif action == "reject":
        fields = {"resolution_code": None, "evidence_ref": None, "notes": None}  # the worker must redo it

    sets = "".join(f"{column} = %s, " for column in fields)
    conn.execute(f"UPDATE tasks SET status = %s, assignee_employee_id = %s, {sets}updated_at = now()"
                 " WHERE task_id = %s", (new_status, assignee, *fields.values(), task_id))
    record_event(conn, tenant_id, "task", task_id, action.upper(), actor.employee_id, from_status=task["status"],
                 to_status=new_status, data=data, request_hash=request_hash)

    if action == "complete":  # every shortfall resolution is verified by someone else (policy v1)
        _transition(conn, tenant_id, task_id, new_status, "submit_for_verification", WORKFLOW,
                    {"reason": "Shortfall resolutions require verification"})
    elif action == "verify":
        resolution = {"task_id": task_id, "resolution_code": task["resolution_code"],
                      "evidence_ref": task["evidence_ref"], "notes": task["notes"],
                      "completed_by": task["assignee_employee_id"], "verified_by": actor.employee_id,
                      "verification_notes": data["notes"]}
        resolved = conn.execute(
            "UPDATE exceptions SET status = 'RESOLVED', resolution = %s WHERE exception_id = %s AND status = 'OPEN'"
            " RETURNING exception_id", (to_jsonb(resolution), task["exception_id"])).fetchone()
        if resolved:  # a case already CLOSED by data stays closed (D-30 edge)
            record_event(conn, tenant_id, "exception", task["exception_id"], "FINDING_RESOLVED",
                         actor.employee_id, from_status="OPEN", to_status="RESOLVED", data=resolution)


def _reassign_check(settings: Settings, roster: list[Candidate], task: dict, new_assignee_id: str) -> dict:
    """Manual assignment gets the same safety check as automatic routing — it can't bypass eligibility."""
    if new_assignee_id == task["assignee_employee_id"]:
        raise AppError(422, "VALIDATION_ERROR", f"Task is already assigned to {new_assignee_id}")
    policy = RoutingPolicy(max_sample_age=settings.workforce_max_sample_age)
    person = next((c for c in roster if c.employee_id == new_assignee_id), None)
    if person is None or person.role not in policy.role_chain or person.location_id != task["canonical_location_id"]:
        raise AppError(422, "INELIGIBLE_ASSIGNEE",
                       f"{new_assignee_id} is not a {' or '.join(policy.role_chain)} at {task['canonical_location_id']}",
                       {"reasons": ["WRONG_ROLE_OR_LOCATION"]})
    check = check_eligibility(person, settings.evaluation_time, policy)
    if not check.eligible:
        raise AppError(422, "INELIGIBLE_ASSIGNEE", f"{new_assignee_id} is not eligible right now",
                       {"reasons": list(check.reasons)})
    return {"employee_id": check.employee_id, "role": check.role, "policy_version": policy.version,
            "as_of": settings.evaluation_time}
