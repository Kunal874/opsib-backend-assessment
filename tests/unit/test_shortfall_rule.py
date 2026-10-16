from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.detection.shortfall import RULE_VERSION, Outcome, evaluate_shortfall
from app.mapping.resolver import ResolvedPoLine, ResolvedReceipt

IST = timezone(timedelta(hours=5, minutes=30))
AS_OF = datetime(2026, 10, 16, 15, 0, tzinfo=IST)
DEADLINE = datetime(2026, 10, 15, 12, 0, tzinfo=IST)


def po(**overrides) -> ResolvedPoLine:
    fields = dict(source_record_id="ERP-PO501-L1", po_number="PO-501", line_number=1, status="OPEN",
                  expected_receipt_by=DEADLINE, canonical_product_id="SKU-001", canonical_location_id="STORE-014",
                  region_id="REGION-MH", expected_qty=Decimal(100), base_uom="EA")
    return ResolvedPoLine(**{**fields, **overrides})


def rc(rid: str, qty, **overrides) -> ResolvedReceipt:
    fields = dict(source_record_id=rid, received_at=DEADLINE - timedelta(hours=1), posting_status="POSTED",
                  canonical_product_id="SKU-001", canonical_location_id="STORE-014",
                  accepted_qty=Decimal(qty), base_uom="EA")
    return ResolvedReceipt(**{**fields, **overrides})


def test_rule_is_versioned():
    assert RULE_VERSION == "receipt_shortfall/v1"


def test_partial_receipts_are_summed():                         # PO-501: 60 + 32 of 100
    e = evaluate_shortfall(po(), [rc("G1", 60), rc("G2", 32)], AS_OF)
    assert (e.outcome, e.expected_qty, e.received_qty, e.variance_qty) == \
           (Outcome.SHORTFALL, Decimal(100), Decimal(92), Decimal(8))
    assert e.counted_receipt_ids == ("G1", "G2")


def test_repeated_receipt_counts_once():                         # PO-506: the same 10 EA record twice
    e = evaluate_shortfall(po(expected_qty=Decimal(20)), [rc("G1", 10), rc("G1", 10)], AS_OF)
    assert (e.received_qty, e.variance_qty) == (Decimal(10), Decimal(10))


def test_exact_match_is_not_a_shortfall():                       # PO-502
    e = evaluate_shortfall(po(expected_qty=Decimal(40)), [rc("G1", 40)], AS_OF)
    assert (e.outcome, e.variance_qty) == (Outcome.NO_SHORTFALL, Decimal(0))


@pytest.mark.parametrize("offset, expected", [(timedelta(seconds=-1), Outcome.NOT_DUE),
                                              (timedelta(0), Outcome.NOT_DUE),
                                              (timedelta(seconds=1), Outcome.SHORTFALL)])
def test_deadline_boundary_is_strict(offset, expected):
    assert evaluate_shortfall(po(), [], DEADLINE + offset).outcome == expected


def test_future_deadline_is_not_due():                           # PO-503
    e = evaluate_shortfall(po(expected_receipt_by=AS_OF + timedelta(days=1)), [rc("G1", 20)], AS_OF)
    assert e.outcome == Outcome.NOT_DUE and e.variance_qty is None


def test_cancelled_line_never_raises():                          # PO-504
    assert evaluate_shortfall(po(status="CANCELLED"), [], AS_OF).outcome == Outcome.CANCELLED


def test_cancelled_wins_even_when_mapping_is_broken():
    e = evaluate_shortfall(po(status="CANCELLED", issue="UNMAPPED_PRODUCT", expected_qty=None), [], AS_OF)
    assert e.outcome == Outcome.CANCELLED


def test_unknown_status_is_not_evaluable():
    e = evaluate_shortfall(po(status="ON_HOLD"), [], AS_OF)
    assert (e.outcome, e.reason) == (Outcome.NOT_EVALUABLE, "UNKNOWN_PO_STATUS")


def test_unposted_and_future_receipts_are_ignored_and_listed():
    e = evaluate_shortfall(po(), [rc("G1", 60), rc("G2", 40, posting_status="DRAFT"),
                                  rc("G3", 40, received_at=AS_OF + timedelta(minutes=1))], AS_OF)
    assert e.variance_qty == Decimal(40)
    assert e.ignored_receipts == (("G2", "NOT_POSTED"), ("G3", "RECEIVED_AFTER_AS_OF"))


def test_receipt_at_exactly_as_of_counts():
    assert evaluate_shortfall(po(), [rc("G1", 100, received_at=AS_OF)], AS_OF).outcome == Outcome.NO_SHORTFALL


def test_late_receipt_after_deadline_still_counts():
    e = evaluate_shortfall(po(), [rc("G1", 100, received_at=DEADLINE + timedelta(hours=5))], AS_OF)
    assert e.outcome == Outcome.NO_SHORTFALL


@pytest.mark.parametrize("override", [{"canonical_product_id": "SKU-999"},
                                      {"canonical_location_id": "STORE-015"},
                                      {"base_uom": "KG"}])
def test_receipt_that_maps_elsewhere_makes_line_not_evaluable(override):
    e = evaluate_shortfall(po(), [rc("G1", 60, **override)], AS_OF)
    assert (e.outcome, e.reason) == (Outcome.NOT_EVALUABLE, "RECEIPT_MISMATCH")


def test_unmapped_counted_receipt_makes_line_not_evaluable():
    e = evaluate_shortfall(po(), [rc("G1", 60, issue="UNMAPPED_PRODUCT", canonical_product_id=None)], AS_OF)
    assert (e.outcome, e.reason) == (Outcome.NOT_EVALUABLE, "RECEIPT_UNMAPPED_PRODUCT")


def test_unmapped_receipt_that_would_be_ignored_does_not_block():
    e = evaluate_shortfall(po(), [rc("G1", 60), rc("G2", 5, posting_status="DRAFT", issue="UNMAPPED_PRODUCT")], AS_OF)
    assert e.outcome == Outcome.SHORTFALL and e.ignored_receipts == (("G2", "NOT_POSTED"),)


def test_unmapped_po_line_is_not_evaluable():
    e = evaluate_shortfall(po(issue="UNMAPPED_PRODUCT", canonical_product_id=None, expected_qty=None), [], AS_OF)
    assert (e.outcome, e.reason) == (Outcome.NOT_EVALUABLE, "UNMAPPED_PRODUCT")


def test_blocked_line_is_not_evaluable():
    e = evaluate_shortfall(po(), [rc("G1", 60)], AS_OF, blocked_by="OPEN_SOURCE_CONFLICT")
    assert (e.outcome, e.reason) == (Outcome.NOT_EVALUABLE, "OPEN_SOURCE_CONFLICT")


def test_tolerance_is_respected():
    assert evaluate_shortfall(po(), [rc("G1", 95)], AS_OF, Decimal(5)).outcome == Outcome.NO_SHORTFALL
    assert evaluate_shortfall(po(), [rc("G1", 94)], AS_OF, Decimal(5)).outcome == Outcome.SHORTFALL


def test_over_receipt_is_not_a_shortfall():
    e = evaluate_shortfall(po(), [rc("G1", 110)], AS_OF)
    assert (e.outcome, e.variance_qty) == (Outcome.NO_SHORTFALL, Decimal(-10))


def test_result_does_not_depend_on_receipt_order():
    a = evaluate_shortfall(po(), [rc("G2", 32), rc("G1", 60)], AS_OF)
    b = evaluate_shortfall(po(), [rc("G1", 60), rc("G2", 32)], AS_OF)
    assert a == b
