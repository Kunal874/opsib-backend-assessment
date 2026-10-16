import time

from app.ai.summary import (DisabledProvider, EvidencePackage, FakeProvider, ProviderUnavailable,
                            build_evidence_package, summarize_exception)

PKG = EvidencePackage(exception_id="e1", exception_type="RECEIPT_SHORTFALL", facts={"variance_qty": "8"},
                      evidence_ids=["ERP-PO501-L1", "WMS-GRN501-A-L1"], unknowns=["ASN not provided"])
GOOD = {"summary": "92 EA were accepted against 100 EA ordered.", "missing_context": ["ASN quantity"],
        "suggested_steps": ["Recount the SKU-001 stock at STORE-014"], "cited_evidence_ids": ["ERP-PO501-L1"]}


def test_valid_output_is_returned():
    r = summarize_exception(FakeProvider(GOOD), PKG)
    assert r["status"] == "OK" and r["error"] is None
    assert r["summary"]["cited_evidence_ids"] == ["ERP-PO501-L1"]


def test_invented_record_id_is_rejected():
    r = summarize_exception(FakeProvider({**GOOD, "cited_evidence_ids": ["ERP-PO501-L1", "WMS-FAKE-1"]}), PKG)
    assert (r["status"], r["summary"]) == ("INVALID", None)
    assert "WMS-FAKE-1" in r["error"]


def test_schema_invalid_output_is_rejected():
    assert summarize_exception(FakeProvider({"summary": ""}), PKG)["status"] == "INVALID"
    assert summarize_exception(FakeProvider({**GOOD, "suggested_steps": ["s"] * 6}), PKG)["status"] == "INVALID"
    assert summarize_exception(FakeProvider("not even a dict"), PKG)["status"] == "INVALID"


def test_model_cannot_smuggle_decisions_in_extra_fields():
    for extra in ({"verified": True}, {"assignee_id": "HR-201"}, {"variance_qty": "0"}):
        assert summarize_exception(FakeProvider({**GOOD, **extra}), PKG)["status"] == "INVALID"


def test_provider_failure_and_timeout_degrade_gracefully():
    assert summarize_exception(DisabledProvider(), PKG)["status"] == "UNAVAILABLE"
    assert summarize_exception(FakeProvider(error=ProviderUnavailable("down")), PKG)["status"] == "UNAVAILABLE"
    assert summarize_exception(FakeProvider(error=RuntimeError("boom")), PKG)["status"] == "UNAVAILABLE"

    class Slow:
        def summarize(self, package):
            time.sleep(1)
            return GOOD

    started = time.monotonic()
    r = summarize_exception(Slow(), PKG, timeout_s=0.05)
    assert r["status"] == "UNAVAILABLE" and "timed out" in r["error"]
    assert time.monotonic() - started < 0.5                       # the caller is not held hostage


EXCEPTION_ROW = {
    "exception_id": "e1", "exception_type": "RECEIPT_SHORTFALL", "po_number": "PO-501", "po_line_number": 1,
    "canonical_product_id": "SKU-001", "canonical_location_id": "STORE-014", "expected_qty": "100",
    "received_qty": "92", "variance_qty": "8", "uom": "EA", "rule_version": "receipt_shortfall/v1",
    "evaluated_as_of": "2026-10-16T15:00:00+05:30",
    "evidence": {"po_source_ids": ["ERP-PO501-L1"], "receipt_source_ids": ["WMS-GRN501-A-L1", "WMS-GRN501-B-L1"],
                 "mapping_refs": ["product_alias:ERP:ERP-SKU-001@i"]},
    "unknowns": ["ASN"], "explanation": "…", "status": "OPEN",
}


def test_package_contains_only_case_evidence():
    pkg = build_evidence_package(EXCEPTION_ROW)
    assert pkg.evidence_ids == ["ERP-PO501-L1", "WMS-GRN501-A-L1", "WMS-GRN501-B-L1"]
    assert pkg.facts["variance_qty"] == "8" and pkg.facts["canonical_location_id"] == "STORE-014"
    assert "HR-" not in pkg.model_dump_json()                     # no employee data leaves the system
