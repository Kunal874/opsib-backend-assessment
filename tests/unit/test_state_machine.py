import pytest

from app.workflows.state_machine import OPEN, InvalidTransition, TaskStatus as S, next_status


def test_happy_path_chain():
    s = S.CREATED
    for action in ("assign", "acknowledge", "start", "complete", "submit_for_verification", "verify"):
        s = next_status(s, action)
    assert s == S.VERIFIED


@pytest.mark.parametrize("state, action", [
    (S.ASSIGNED, "complete"), (S.CREATED, "acknowledge"), (S.COMPLETED, "verify"), (S.VERIFIED, "reassign"),
    (S.PENDING_VERIFICATION, "complete"), (S.IN_PROGRESS, "fly"), (S.PENDING_VERIFICATION, "cancel"),
    (S.CANCELLED, "start"), (S.ASSIGNED, "assign"), (S.VERIFIED, "cancel"),
])
def test_invalid_transitions_raise(state, action):
    with pytest.raises(InvalidTransition):
        next_status(state, action)


def test_block_reject_cancel_reopen_paths():
    assert next_status(next_status(S.IN_PROGRESS, "block"), "unblock") == S.IN_PROGRESS
    assert next_status(S.PENDING_VERIFICATION, "reject") == S.IN_PROGRESS
    assert next_status(next_status(S.ACKNOWLEDGED, "cancel"), "reopen") == S.CREATED


def test_reassign_is_allowed_from_every_open_state_only():
    for state in S:
        if state in OPEN:
            assert next_status(state, "reassign") == S.ASSIGNED
        else:
            with pytest.raises(InvalidTransition):
                next_status(state, "reassign")


def test_status_strings_are_accepted():
    assert next_status("ASSIGNED", "acknowledge") == S.ACKNOWLEDGED
