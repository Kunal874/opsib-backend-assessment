"""The task state machine — the single source of truth for allowed transitions (D-38).

Clients never set a status: they call an action, and the server looks it up here. Anything not in the
table raises InvalidTransition. cancel / reopen / assign / submit_for_verification are system-only actions.
The audit event_type for any transition is the action in upper case (ASSIGN, ACKNOWLEDGE, ...).
"""
from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    CANCELLED = "CANCELLED"


S = TaskStatus
OPEN = frozenset({S.CREATED, S.ASSIGNED, S.ACKNOWLEDGED, S.IN_PROGRESS, S.BLOCKED})

TRANSITIONS: dict[str, tuple[frozenset[TaskStatus], TaskStatus]] = {
    "assign": (frozenset({S.CREATED}), S.ASSIGNED),
    "reassign": (OPEN, S.ASSIGNED),
    "acknowledge": (frozenset({S.ASSIGNED}), S.ACKNOWLEDGED),
    "start": (frozenset({S.ACKNOWLEDGED}), S.IN_PROGRESS),
    "block": (frozenset({S.IN_PROGRESS}), S.BLOCKED),
    "unblock": (frozenset({S.BLOCKED}), S.IN_PROGRESS),
    "complete": (frozenset({S.IN_PROGRESS}), S.COMPLETED),
    "submit_for_verification": (frozenset({S.COMPLETED}), S.PENDING_VERIFICATION),
    "verify": (frozenset({S.PENDING_VERIFICATION}), S.VERIFIED),
    "reject": (frozenset({S.PENDING_VERIFICATION}), S.IN_PROGRESS),
    "cancel": (OPEN, S.CANCELLED),
    "reopen": (frozenset({S.CANCELLED}), S.CREATED),
}


class InvalidTransition(Exception):
    def __init__(self, current: str, action: str):
        super().__init__(f"Cannot '{action}' a task that is {current}")
        self.current = current
        self.action = action


def next_status(current: str, action: str) -> TaskStatus:
    allowed_from, target = TRANSITIONS.get(action, (frozenset(), None))
    if TaskStatus(current) not in allowed_from:
        raise InvalidTransition(str(current), action)
    return target
