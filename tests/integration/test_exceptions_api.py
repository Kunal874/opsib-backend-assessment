"""Inspecting exceptions with their supporting source records, and the AI summary boundary over HTTP."""
import pytest

from app.ai.summary import FakeProvider
from app.detection.service import run_detection
from tests.conftest import load_fixture_pack


@pytest.fixture
def loaded(client, conn, settings):
    load_fixture_pack(conn, settings)
    run_detection(conn, settings)
    return {r["po_number"]: str(r["exception_id"])
            for r in conn.execute("SELECT po_number, exception_id FROM exceptions")}


def test_list_exceptions_with_filters(client, loaded):
    all_open = client.get("/exceptions").json()
    assert [(e["po_number"], e["variance_qty"], e["status"]) for e in all_open] == [
        ("PO-501", "8", "OPEN"), ("PO-505", "24", "OPEN"), ("PO-506", "10", "OPEN")]
    assert [e["po_number"] for e in client.get("/exceptions", params={"location_id": "STORE-015"}).json()] == ["PO-505"]
    assert client.get("/exceptions", params={"status": "RESOLVED"}).json() == []
    assert client.get("/exceptions", params={"status": "MAYBE"}).status_code == 422


def test_exception_detail_shows_source_records_down_to_the_csv_line(client, loaded):
    body = client.get(f"/exceptions/{loaded['PO-506']}").json()
    assert (body["business_key"], body["variance_qty"], body["uom"]) == ("RECEIPT_SHORTFALL|PO-506|1", "10", "EA")
    po_rows = body["source_records"]["purchase_order_lines"]
    receipts = body["source_records"]["goods_receipt_lines"]
    assert [(r["source_record_id"], r["source_row_number"]) for r in po_rows] == [("ERP-PO506-L1", 7)]
    assert [(r["source_record_id"], r["accepted_qty"], r["source_row_number"]) for r in receipts] == \
           [("WMS-GRN506-A-L1", "10", 7)]                         # counted once; first occurrence is line 7
    assert receipts[0]["raw_row"]["accepted_qty"] == "10"           # exactly as the CSV said it
    assert body["tasks"][0]["assignee_employee_id"] == "HR-102"
    assert [h["event_type"] for h in body["history"]] == ["FINDING_CREATED"]


def test_unknown_exception_is_404(client, loaded):
    assert client.get("/exceptions/00000000-0000-0000-0000-000000000000").status_code == 404


def test_ai_summary_is_unavailable_by_default(client, loaded):
    r = client.get(f"/exceptions/{loaded['PO-501']}/ai-summary")
    assert r.status_code == 200 and r.json()["status"] == "UNAVAILABLE" and r.json()["summary"] is None


def test_ai_summary_with_a_provider_is_validated_against_the_evidence(client, loaded):
    good = {"summary": "92 EA accepted against 100 EA ordered.", "cited_evidence_ids": ["WMS-GRN501-B-L1"],
            "suggested_steps": ["Ask the supplier for the ASN"], "missing_context": ["dispatch quantity"]}
    client.app.state.summary_provider = FakeProvider(good)
    ok = client.get(f"/exceptions/{loaded['PO-501']}/ai-summary").json()
    assert ok["status"] == "OK" and ok["summary"]["cited_evidence_ids"] == ["WMS-GRN501-B-L1"]

    # The same output cites a PO-501 receipt, so for PO-505 it is an invented reference.
    other_case = client.get(f"/exceptions/{loaded['PO-505']}/ai-summary").json()
    assert other_case["status"] == "INVALID"
