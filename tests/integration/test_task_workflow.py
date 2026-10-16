"""The task lifecycle over HTTP: server-enforced transitions, permissions, safe retries, audit history."""
import threading

import pytest

from app.db import connect
from app.detection.service import run_detection
from app.errors import AppError
from app.ingestion.importer import import_csv
from app.workflows.tasks import perform_action
from tests.conftest import DATA, load_fixture_pack

DONE = {"resolution_code": "SHORTFALL_CONFIRMED", "evidence_ref": "RECOUNT-0042"}


@pytest.fixture
def tasks(client, conn, settings):
    load_fixture_pack(conn, settings)
    run_detection(conn, settings)
    rows = conn.execute("SELECT e.po_number, t.task_id FROM tasks t JOIN exceptions e USING (exception_id)").fetchall()
    return {r["po_number"]: str(r["task_id"]) for r in rows}


def act(client, task_id, action, actor, body=None):
    return client.post(f"/tasks/{task_id}/{action}", json=body or {}, headers={"X-Actor-Id": actor})


def history(client, task_id):
    return [(h["event_type"], h["actor"]) for h in client.get(f"/tasks/{task_id}").json()["history"]]


def start_work(client, task_id, actor):
    assert act(client, task_id, "acknowledge", actor).status_code == 200
    assert act(client, task_id, "start", actor).status_code == 200


# ------------------------------------------------------------------ reading

def test_list_tasks_by_assignee_and_location(client, tasks):
    mine = client.get("/tasks", params={"assignee_id": "HR-102"}).json()
    assert sorted(t["po_number"] for t in mine) == ["PO-501", "PO-506"]
    assert all(t["status"] == "ASSIGNED" and t["due_at"] and t["variance_qty"] for t in mine)
    store_015 = client.get("/tasks", params={"location_id": "STORE-015"}).json()
    assert [(t["po_number"], t["assignee_employee_id"]) for t in store_015] == [("PO-505", "HR-401")]
    assert client.get("/tasks", params={"status": "VERIFIED"}).json() == []
    assert client.get("/tasks", params={"status": "DONE"}).status_code == 422


def test_task_detail_and_unknown_task(client, tasks):
    body = client.get(f"/tasks/{tasks['PO-505']}").json()
    assert (body["exception"]["business_key"], body["exception"]["variance_qty"]) == ("RECEIPT_SHORTFALL|PO-505|1", "24")
    assert client.get("/tasks/00000000-0000-0000-0000-000000000000").status_code == 404


# ------------------------------------------------------------------ permissions and transitions

def test_only_assignee_can_work_the_task(client, tasks):
    r = act(client, tasks["PO-501"], "acknowledge", "HR-101")
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_or_inactive_actor_is_forbidden(client, tasks):
    assert act(client, tasks["PO-501"], "acknowledge", "HR-999").status_code == 403
    assert act(client, tasks["PO-501"], "acknowledge", "HR-103").status_code == 403      # INACTIVE in HRMS


def test_actor_header_is_required(client, tasks):
    r = client.post(f"/tasks/{tasks['PO-501']}/acknowledge", json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_cannot_skip_states(client, tasks):
    r = act(client, tasks["PO-501"], "complete", "HR-102", DONE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_TRANSITION"
    assert r.json()["error"]["details"] == {"status": "ASSIGNED", "action": "complete"}


def test_full_lifecycle_with_retry_safety_and_verification(client, conn, tasks):
    t = tasks["PO-501"]
    start_work(client, t, "HR-102")
    first = act(client, t, "complete", "HR-102", DONE)
    assert first.status_code == 200 and first.json()["task"]["status"] == "PENDING_VERIFICATION"
    assert first.json()["replayed"] is False
    events_before = len(history(client, t))

    retry = act(client, t, "complete", "HR-102", DONE)                     # client timed out, retries
    assert retry.status_code == 200 and retry.json()["replayed"] is True
    assert retry.json()["task"]["status"] == "PENDING_VERIFICATION"
    assert len(history(client, t)) == events_before                       # nothing written twice

    different = act(client, t, "complete", "HR-102", {**DONE, "evidence_ref": "OTHER-1"})
    assert different.status_code == 409 and different.json()["error"]["code"] == "CONFLICTING_RETRY"

    assert act(client, t, "verify", "HR-102").status_code == 403           # no self-verification
    v = act(client, t, "verify", "HR-201")
    assert v.status_code == 200 and v.json()["task"]["status"] == "VERIFIED"
    exc = conn.execute("SELECT e.status, e.resolution FROM exceptions e JOIN tasks t USING (exception_id)"
                       " WHERE t.task_id = %s", (t,)).fetchone()
    assert exc["status"] == "RESOLVED"
    resolution = exc["resolution"]
    assert (resolution["resolution_code"], resolution["evidence_ref"]) == ("SHORTFALL_CONFIRMED", "RECOUNT-0042")
    assert (resolution["completed_by"], resolution["verified_by"], resolution["task_id"]) == ("HR-102", "HR-201", t)

    assert history(client, t) == [
        ("TASK_CREATED", "system:detection"), ("ASSIGN", "system:router"), ("ACKNOWLEDGE", "HR-102"),
        ("START", "HR-102"), ("COMPLETE", "HR-102"), ("SUBMIT_FOR_VERIFICATION", "system:workflow"),
        ("VERIFY", "HR-201")]


def test_repeated_acknowledge_is_a_safe_replay(client, tasks):
    assert act(client, tasks["PO-506"], "acknowledge", "HR-102").json()["replayed"] is False
    again = act(client, tasks["PO-506"], "acknowledge", "HR-102")
    assert again.status_code == 200 and again.json()["replayed"] is True


def test_complete_requires_known_code_and_real_evidence(client, tasks):
    t = tasks["PO-506"]
    start_work(client, t, "HR-102")
    assert act(client, t, "complete", "HR-102", {**DONE, "evidence_ref": "  "}).status_code == 422
    assert act(client, t, "complete", "HR-102", {**DONE, "resolution_code": "SUPPLIER_FAULT"}).status_code == 422
    assert act(client, t, "complete", "HR-102", {**DONE, "resolution_code": "OTHER"}).status_code == 422  # needs notes
    ok = act(client, t, "complete", "HR-102", {**DONE, "resolution_code": "OTHER", "notes": "pallet mislabelled"})
    assert ok.status_code == 200


def test_block_unblock_and_reject_paths(client, conn, tasks):
    t = tasks["PO-506"]
    start_work(client, t, "HR-102")
    assert act(client, t, "block", "HR-102", {"reason": ""}).status_code == 422
    assert act(client, t, "block", "HR-102", {"reason": "waiting for recount"}).json()["task"]["status"] == "BLOCKED"
    assert act(client, t, "unblock", "HR-102").json()["task"]["status"] == "IN_PROGRESS"
    act(client, t, "complete", "HR-102", DONE)
    rejected = act(client, t, "reject", "HR-201", {"reason": "evidence photo unreadable"})
    assert rejected.json()["task"]["status"] == "IN_PROGRESS"
    assert rejected.json()["task"]["evidence_ref"] is None                 # must be re-submitted
    redo = act(client, t, "complete", "HR-102", {**DONE, "evidence_ref": "RECOUNT-0043"})
    assert redo.status_code == 200 and redo.json()["replayed"] is False
    assert conn.execute("SELECT status FROM exceptions WHERE po_number = 'PO-506'").fetchone()["status"] == "OPEN"


def test_manager_cannot_verify_own_task_but_regional_can(client, tasks):
    t = tasks["PO-505"]                                                    # routed to HR-401 (store manager)
    start_work(client, t, "HR-401")
    act(client, t, "complete", "HR-401", DONE)
    assert act(client, t, "verify", "HR-401").status_code == 403
    assert act(client, t, "verify", "HR-201").status_code == 403           # manager of a different store
    assert act(client, t, "verify", "HR-501").status_code == 200           # regional ops, same region


def test_reassign_records_reason_and_rejects_ineligible(client, tasks):
    t = tasks["PO-501"]
    bad = act(client, t, "reassign", "HR-201", {"new_assignee_id": "HR-101", "reason": "cover"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INELIGIBLE_ASSIGNEE"
    assert bad.json()["error"]["details"]["reasons"] == ["OFF_SHIFT"]
    assert act(client, t, "reassign", "HR-102", {"new_assignee_id": "HR-201", "reason": "x"}).status_code == 403
    ok = act(client, t, "reassign", "HR-201", {"new_assignee_id": "HR-201", "reason": "associate busy"})
    assert ok.status_code == 200 and ok.json()["task"]["assignee_employee_id"] == "HR-201"
    last = client.get(f"/tasks/{t}").json()["history"][-1]
    assert (last["event_type"], last["actor"], last["data"]["reason"]) == ("REASSIGN", "HR-201", "associate busy")
    assert (last["from_status"], last["to_status"]) == ("ASSIGNED", "ASSIGNED")


def test_manual_assignment_cannot_bypass_eligibility(client, conn, settings):
    load_fixture_pack(conn, settings)
    import_csv(conn, settings.tenant_id, "workforce_status", (DATA / "workforce_hr401_off.csv").read_bytes())
    run_detection(conn, settings)
    t = str(conn.execute("SELECT t.task_id FROM tasks t JOIN exceptions e USING (exception_id)"
                         " WHERE e.po_number = 'PO-505'").fetchone()["task_id"])
    r = act(client, t, "reassign", "HR-501", {"new_assignee_id": "HR-401", "reason": "manual pick"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INELIGIBLE_ASSIGNEE"


# ------------------------------------------------------------------ concurrency (Task C design question)

def test_two_simultaneous_completions_one_wins_one_is_told(conn, settings, tasks, client):
    t = tasks["PO-501"]
    start_work(client, t, "HR-102")
    barrier, results = threading.Barrier(2), []

    def worker(evidence):
        with connect(settings.database_url) as c:
            barrier.wait()
            try:
                results.append(("ok", perform_action(c, settings, t, "complete", "HR-102",
                                                     {**DONE, "evidence_ref": evidence})["replayed"]))
            except AppError as exc:
                results.append(("error", exc.code))

    threads = [threading.Thread(target=worker, args=(ev,)) for ev in ("A-1", "B-2")]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert sorted(results) == [("error", "CONFLICTING_RETRY"), ("ok", False)]
    completes = conn.execute("SELECT count(*) AS n FROM audit_events WHERE entity_id = %s AND event_type = 'COMPLETE'",
                             (t,)).fetchone()["n"]
    assert completes == 1
