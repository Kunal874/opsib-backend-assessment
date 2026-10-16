from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.ingestion.datasets import DATASETS
from app.ingestion.rows import EmployeeRow, PurchaseOrderLineRow, WorkforceStatusRow, content_hash

GOOD_PO = {
    "source_record_id": "ERP-PO501-L1", "po_number": "PO-501", "line_number": "1", "supplier_code": "SUP-07",
    "location_code": "MUM-014", "item_code": "ERP-SKU-001", "ordered_qty": "100", "ordered_uom": "EA",
    "status": "OPEN", "expected_receipt_by": "2026-10-15T12:00:00+05:30",
}
GOOD_SAMPLE = {
    "source_employee_id": "HR-102", "availability_status": "ON_SHIFT", "shift_start": "2026-10-16T09:00:00+05:30",
    "shift_end": "2026-10-16T18:00:00+05:30", "observed_at": "2026-10-16T14:50:00+05:30",
}


def test_valid_po_row_parses_types():
    row = PurchaseOrderLineRow.model_validate(GOOD_PO)
    assert row.ordered_qty == Decimal("100") and row.line_number == 1
    assert row.expected_receipt_by.utcoffset().total_seconds() == 5.5 * 3600


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValidationError) as e:
        PurchaseOrderLineRow.model_validate({**GOOD_PO, "expected_receipt_by": "2026-10-15T12:00:00"})
    assert "expected_receipt_by" in str(e.value)


@pytest.mark.parametrize("qty", ["0", "-5", "abc", "", "NaN"])
def test_bad_quantity_is_rejected(qty):
    with pytest.raises(ValidationError):
        PurchaseOrderLineRow.model_validate({**GOOD_PO, "ordered_qty": qty})


@pytest.mark.parametrize("field", ["po_number", "source_record_id"])
def test_blank_required_text_is_rejected(field):
    with pytest.raises(ValidationError):
        PurchaseOrderLineRow.model_validate({**GOOD_PO, field: "   "})


def test_status_is_free_text_so_unknown_values_reach_the_rule():
    assert PurchaseOrderLineRow.model_validate({**GOOD_PO, "status": "ON_HOLD"}).status == "ON_HOLD"


def test_blank_optional_employee_location_becomes_none():
    row = EmployeeRow.model_validate({
        "source_employee_id": "HR-501", "display_name": "x", "employment_status": "ACTIVE",
        "role": "regional_operations", "location_code": "", "manager_employee_id": "", "region_id": "REGION-MH"})
    assert row.location_code is None and row.manager_employee_id is None and row.region_id == "REGION-MH"


def test_extra_columns_are_ignored():
    assert PurchaseOrderLineRow.model_validate({**GOOD_PO, "unexpected": "x"}).po_number == "PO-501"


def test_hash_ignores_formatting_noise():
    a = PurchaseOrderLineRow.model_validate(GOOD_PO)
    b = PurchaseOrderLineRow.model_validate(
        {**GOOD_PO, "ordered_qty": "100.00", "expected_receipt_by": "2026-10-15T06:30:00Z", "po_number": " PO-501 "})
    assert content_hash(a) == content_hash(b)


def test_hash_changes_when_business_content_changes():
    a = PurchaseOrderLineRow.model_validate(GOOD_PO)
    b = PurchaseOrderLineRow.model_validate({**GOOD_PO, "ordered_qty": "90"})
    assert content_hash(a) != content_hash(b)


def test_registry_keys_and_required_columns():
    po = DATASETS["purchase_order_lines"]
    assert po.source_key(PurchaseOrderLineRow.model_validate(GOOD_PO)) == "ERP-PO501-L1"
    assert set(DATASETS) == {"purchase_order_lines", "goods_receipt_lines", "product_aliases",
                             "location_aliases", "employees", "workforce_status"}
    assert "expected_receipt_by" in po.required_columns
    assert DATASETS["product_aliases"].key_fields == ("source_system", "external_item_id")


def test_time_in_a_source_key_is_the_instant_not_the_spelling():
    spec = DATASETS["workforce_status"]
    ist = WorkforceStatusRow.model_validate(GOOD_SAMPLE)
    utc = WorkforceStatusRow.model_validate({**GOOD_SAMPLE, "observed_at": "2026-10-16T09:20:00Z"})
    assert spec.source_key(ist) == spec.source_key(utc) == "HR-102|2026-10-16T09:20:00+00:00"
