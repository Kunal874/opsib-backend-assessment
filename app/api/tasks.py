"""Task endpoints. Every change goes through perform_action(); routes only parse input (D-38..D-41).

X-Actor-Id identifies the person acting. It stands in for real authentication and is documented as such.
"""
import uuid
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app.api.deps import get_conn, get_settings
from app.config import Settings
from app.workflows.state_machine import TaskStatus
from app.workflows.tasks import get_task, list_tasks, perform_action

router = APIRouter(tags=["tasks"])

ActorId = Annotated[str, Header(alias="X-Actor-Id", min_length=1,
                                description="Employee ID of the person acting (stand-in for authentication)")]
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
Config = Annotated[Settings, Depends(get_settings)]


class ReasonBody(BaseModel):
    reason: str


class CompleteBody(BaseModel):
    resolution_code: str
    evidence_ref: str
    notes: str | None = None


class VerifyBody(BaseModel):
    notes: str | None = None


class ReassignBody(BaseModel):
    new_assignee_id: str
    reason: str


@router.get("/tasks", summary="List tasks by assignee, location and/or status (ordered by due time)")
def read_tasks(conn: Conn, settings: Config, assignee_id: str | None = None, location_id: str | None = None,
               status: TaskStatus | None = None) -> list[dict]:
    return list_tasks(conn, settings.tenant_id, assignee_id, location_id, status)


@router.get("/tasks/{task_id}", summary="One task with its exception summary and full audit history")
def read_task(task_id: uuid.UUID, conn: Conn, settings: Config) -> dict:
    return get_task(conn, settings.tenant_id, task_id)


def _act(conn, settings, task_id, action, actor, body: BaseModel | None = None) -> dict:
    return perform_action(conn, settings, task_id, action, actor, body.model_dump() if body else {})


@router.post("/tasks/{task_id}/acknowledge", summary="Assignee accepts the task")
def acknowledge(task_id: uuid.UUID, actor: ActorId, conn: Conn, settings: Config) -> dict:
    return _act(conn, settings, task_id, "acknowledge", actor)


@router.post("/tasks/{task_id}/start", summary="Assignee starts working")
def start(task_id: uuid.UUID, actor: ActorId, conn: Conn, settings: Config) -> dict:
    return _act(conn, settings, task_id, "start", actor)


@router.post("/tasks/{task_id}/block", summary="Assignee is blocked (reason required)")
def block(task_id: uuid.UUID, body: ReasonBody, actor: ActorId, conn: Conn, settings: Config) -> dict:
    return _act(conn, settings, task_id, "block", actor, body)


@router.post("/tasks/{task_id}/unblock", summary="Assignee resumes work")
def unblock(task_id: uuid.UUID, actor: ActorId, conn: Conn, settings: Config) -> dict:
    return _act(conn, settings, task_id, "unblock", actor)


@router.post("/tasks/{task_id}/complete",
             summary="Assignee records resolution code + evidence; goes to PENDING_VERIFICATION. Safe to retry.")
def complete(task_id: uuid.UUID, body: CompleteBody, actor: ActorId, conn: Conn, settings: Config) -> dict:
    return _act(conn, settings, task_id, "complete", actor, body)


@router.post("/tasks/{task_id}/verify", summary="Supervisor (not the assignee) verifies; resolves the exception")
def verify(task_id: uuid.UUID, actor: ActorId, conn: Conn, settings: Config, body: VerifyBody | None = None) -> dict:
    return _act(conn, settings, task_id, "verify", actor, body or VerifyBody())


@router.post("/tasks/{task_id}/reject", summary="Supervisor sends the work back (reason required)")
def reject(task_id: uuid.UUID, body: ReasonBody, actor: ActorId, conn: Conn, settings: Config) -> dict:
    return _act(conn, settings, task_id, "reject", actor, body)


@router.post("/tasks/{task_id}/reassign", summary="Supervisor reassigns to an eligible employee (reason required)")
def reassign(task_id: uuid.UUID, body: ReassignBody, actor: ActorId, conn: Conn, settings: Config) -> dict:
    return _act(conn, settings, task_id, "reassign", actor, body)
