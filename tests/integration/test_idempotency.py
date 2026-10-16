"""Re-running the whole pipeline, or running it concurrently, must not change the business result."""
import threading

from app.db import connect
from app.detection.service import run_detection
from tests.conftest import load_fixture_pack


def snapshot(conn) -> dict:
    return {
        "exceptions": conn.execute(
            "SELECT exception_id, status, expected_qty, received_qty, variance_qty, evidence, explanation"
            " FROM exceptions ORDER BY exception_id").fetchall(),
        "tasks": conn.execute("SELECT task_id, status, assignee_employee_id FROM tasks ORDER BY task_id").fetchall(),
        "facts": [conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                  for table in ("purchase_order_lines", "goods_receipt_lines", "employees", "workforce_status")],
        "audit_events": conn.execute("SELECT count(*) AS n FROM audit_events").fetchone()["n"],
    }


def test_importing_and_detecting_twice_changes_nothing(conn, settings):
    load_fixture_pack(conn, settings)
    run_detection(conn, settings)
    first = snapshot(conn)
    load_fixture_pack(conn, settings)
    second_run = run_detection(conn, settings)
    assert snapshot(conn) == first
    assert second_run["exceptions"] == {"created": 0, "updated": 0, "unchanged": 3, "reopened": 0, "closed": 0}


def test_concurrent_detection_creates_each_exception_once(conn, settings):
    load_fixture_pack(conn, settings)
    barrier, errors = threading.Barrier(3), []

    def worker():
        try:
            with connect(settings.database_url) as c:
                barrier.wait()
                run_detection(c, settings)
        except Exception as exc:  # surface thread failures in the main test
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert conn.execute("SELECT count(*) AS n FROM exceptions").fetchone()["n"] == 3
    created = conn.execute("SELECT count(*) AS n FROM audit_events WHERE event_type = 'FINDING_CREATED'").fetchone()
    assert created["n"] == 3
    assert conn.execute("SELECT count(*) AS n FROM tasks").fetchone()["n"] == 3
