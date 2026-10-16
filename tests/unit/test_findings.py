from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.detection.findings import build_finding, business_key, exception_id
from app.detection.shortfall import Evaluation, Outcome
from app.mapping.resolver import ResolvedPoLine

IST = timezone(timedelta(hours=5, minutes=30))
AS_OF = datetime(2026, 10, 16, 15, tzinfo=IST)
PO = ResolvedPoLine(
    source_record_id="ERP-PO505-L1", po_number="PO-505", line_number=1, status="OPEN",
    expected_receipt_by=datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc),   # = 15:00 IST
    canonical_product_id="SKU-002", canonical_location_id="STORE-015", region_id="REGION-MH",
    expected_qty=Decimal(120), base_uom="EA", ordered_qty=Decimal(10), ordered_uom="BOX",
    mapping_refs=("product_alias:ERP:ERP-BOX-12@i", "location_alias:ERP:MUM-015@i"))
EV = Evaluation(Outcome.SHORTFALL, expected_qty=Decimal(120), received_qty=Decimal(96), variance_qty=Decimal(24),
                counted_receipt_ids=("WMS-GRN505-A-L1",))
RECEIPT_REFS = ["product_alias:WMS:WMS-MAT-2002@i", "location_alias:WMS:WH-MH-015@i"]


def test_identity_is_stable_and_tenant_scoped():
    assert business_key("PO-505", 1) == "RECEIPT_SHORTFALL|PO-505|1"
    assert exception_id("t1", "k") == exception_id("t1", "k")
    assert exception_id("t1", "k") != exception_id("t2", "k")


def test_finding_carries_numbers_and_identity():
    f = build_finding("t1", PO, EV, AS_OF, RECEIPT_REFS)
    assert f["exception_id"] == exception_id("t1", "RECEIPT_SHORTFALL|PO-505|1")
    assert (f["expected_qty"], f["received_qty"], f["variance_qty"], f["uom"]) == \
           (Decimal(120), Decimal(96), Decimal(24), "EA")
    assert (f["rule_version"], f["canonical_location_id"], f["evaluated_as_of"]) == \
           ("receipt_shortfall/v1", "STORE-015", AS_OF)


def test_finding_is_traceable_to_sources_and_mappings():
    evidence = build_finding("t1", PO, EV, AS_OF, RECEIPT_REFS)["evidence"]
    assert evidence["po_source_ids"] == ["ERP-PO505-L1"]
    assert evidence["receipt_source_ids"] == ["WMS-GRN505-A-L1"]
    assert evidence["mapping_refs"] == sorted([*PO.mapping_refs, *RECEIPT_REFS])
    assert evidence["mapping_version"] == "mapping/v1"


def test_explanation_states_facts_only_and_unknowns_name_missing_evidence():
    f = build_finding("t1", PO, EV, AS_OF, RECEIPT_REFS)
    text = f["explanation"]
    assert "24 EA" in text and "10 BOX × 12" in text and "2026-10-15T15:00:00+05:30" in text
    assert not any(word in text.lower() for word in ("fault", "short-shipped", "lost", "blame"))
    assert any("ASN" in u for u in f["unknowns"]) and any("carrier" in u.lower() for u in f["unknowns"])


def test_same_inputs_give_the_same_finding():
    assert build_finding("t1", PO, EV, AS_OF, RECEIPT_REFS) == build_finding("t1", PO, EV, AS_OF, list(reversed(RECEIPT_REFS)))
