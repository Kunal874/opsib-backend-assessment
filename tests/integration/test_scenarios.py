"""The six required scenario outcomes, end to end: real fixtures -> importer -> resolver -> rule -> database."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.detection.service import run_detection
from app.ingestion.importer import import_csv
from tests.conftest import load_fixture_pack

IST = timezone(timedelta(hours=5, minutes=30))
LATE_RECEIPT = (
    "source_record_id,receipt_number,receipt_line,po_number,po_line_number,location_code,product_ref,"
    "accepted_qty,uom,received_at,posting_status\n"
    "WMS-GRN501-C-L1,GRN-501-C,1,PO-501,1,WH-MH-014,WMS-BRC-001,8,EA,2026-10-16T13:00:00+05:30,POSTED\n"
).encode()


def lines_by_po(run):
    return {line["po_number"]: line for line in run["lines"]}


def test_six_required_scenarios(conn, settings):
    load_fixture_pack(conn, settings)
    lines = lines_by_po(run_detection(conn, settings))
    assert (lines["PO-501"]["outcome"], lines["PO-501"]["received_qty"], lines["PO-501"]["variance_qty"]) == \
           ("SHORTFALL", "92", "8")                                   # split receipts 60 + 32
    assert (lines["PO-502"]["outcome"], lines["PO-502"]["variance_qty"]) == ("NO_SHORTFALL", "0")
    assert lines["PO-503"]["outcome"] == "NOT_DUE"                    # deadline still in the future
    assert lines["PO-504"]["outcome"] == "CANCELLED"
    assert (lines["PO-505"]["outcome"], lines["PO-505"]["expected_qty"], lines["PO-505"]["variance_qty"]) == \
           ("SHORTFALL", "120", "24")                                 # 10 BOX x 12 vs 96 EA
    assert (lines["PO-506"]["outcome"], lines["PO-506"]["variance_qty"]) == ("SHORTFALL", "10")  # repeat counted once
    rows = conn.execute("SELECT po_number, variance_qty, canonical_location_id, status FROM exceptions"
                        " ORDER BY po_number").fetchall()
    assert [(r["po_number"], r["variance_qty"], r["canonical_location_id"], r["status"]) for r in rows] == [
        ("PO-501", Decimal(8), "STORE-014", "OPEN"),
        ("PO-505", Decimal(24), "STORE-015", "OPEN"),
        ("PO-506", Decimal(10), "STORE-014", "OPEN"),
    ]


def test_every_finding_traces_to_source_rows(conn, settings):
    load_fixture_pack(conn, settings)
    run_detection(conn, settings)
    finding = conn.execute("SELECT evidence, rule_version FROM exceptions WHERE po_number = 'PO-501'").fetchone()
    evidence = finding["evidence"]
    assert finding["rule_version"] == "receipt_shortfall/v1"
    assert evidence["po_source_ids"] == ["ERP-PO501-L1"]
    assert evidence["receipt_source_ids"] == ["WMS-GRN501-A-L1", "WMS-GRN501-B-L1"]
    assert any(ref.startswith("location_alias:WMS:WH-MH-014@") for ref in evidence["mapping_refs"])
    for source_id in evidence["po_source_ids"] + evidence["receipt_source_ids"]:
        raw = conn.execute("SELECT 1 FROM import_rows WHERE source_key = %s AND outcome = 'ACCEPTED'",
                           (source_id,)).fetchone()
        assert raw, f"{source_id} has no accepted raw row"


def test_detection_run_is_recorded_with_every_line(conn, settings):
    load_fixture_pack(conn, settings)
    run = run_detection(conn, settings)
    stored = conn.execute("SELECT as_of, rule_version, line_outcomes FROM detection_runs WHERE run_id = %s",
                          (run["run_id"],)).fetchone()
    assert stored["as_of"] == settings.evaluation_time and stored["rule_version"] == "receipt_shortfall/v1"
    assert sorted(line["po_number"] for line in stored["line_outcomes"]) == \
           ["PO-501", "PO-502", "PO-503", "PO-504", "PO-505", "PO-506"]


def test_late_receipt_closes_the_finding_without_duplicating(conn, settings):
    load_fixture_pack(conn, settings)
    run_detection(conn, settings)
    import_csv(conn, settings.tenant_id, "goods_receipt_lines", LATE_RECEIPT)
    run = run_detection(conn, settings)
    assert run["exceptions"]["closed"] == 1
    assert conn.execute("SELECT status FROM exceptions WHERE po_number = 'PO-501'").fetchone()["status"] == "CLOSED"
    assert conn.execute("SELECT count(*) AS n FROM exceptions").fetchone()["n"] == 3
    event = conn.execute("SELECT data FROM audit_events WHERE event_type = 'FINDING_CLOSED'").fetchone()
    assert "WMS-GRN501-C-L1" in event["data"]["receipt_source_ids"]


def test_shortfall_that_reappears_reopens_the_same_finding(conn, settings):
    load_fixture_pack(conn, settings)
    import_csv(conn, settings.tenant_id, "goods_receipt_lines", LATE_RECEIPT)
    run_detection(conn, settings)                                       # 100 received -> no PO-501 finding yet
    run_detection(conn, settings, as_of=datetime(2026, 10, 16, 12, tzinfo=IST))   # before the late receipt
    run_detection(conn, settings)                                       # late receipt counts again -> closed
    early = run_detection(conn, settings, as_of=datetime(2026, 10, 16, 12, tzinfo=IST))
    assert early["exceptions"]["reopened"] == 1
    events = [r["event_type"] for r in conn.execute(
        "SELECT event_type FROM audit_events a JOIN exceptions e ON a.entity_id = e.exception_id::text"
        " WHERE e.po_number = 'PO-501' ORDER BY event_id")]
    assert events == ["FINDING_CREATED", "FINDING_CLOSED", "FINDING_REOPENED"]


def test_detection_over_http(client, conn, settings):
    load_fixture_pack(conn, settings)
    r = client.post("/detection/runs")
    assert r.status_code == 201
    assert r.json()["counts"] == {"SHORTFALL": 3, "NO_SHORTFALL": 1, "NOT_DUE": 1, "CANCELLED": 1, "NOT_EVALUABLE": 0}
    later = client.post("/detection/runs", params={"as_of": "2026-10-18T09:00:00+05:30"})
    assert lines_by_po(later.json())["PO-503"]["outcome"] == "SHORTFALL"      # 50 ordered, 20 received
    naive = client.post("/detection/runs", params={"as_of": "2026-10-18T09:00:00"})
    assert naive.status_code == 422
