"""Typed row models — one per supplied CSV; field names are exactly the CSV headers.

Parsers check *shape*: types, required fields, ranges and time zones. They do not decide what
status words mean — the rules do that with allow-lists, so an unknown value fails safe and stays
visible instead of vanishing as a rejected row (D-13, D-52).
"""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, PositiveInt, StringConstraints


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
OptionalText = Annotated[Text | None, BeforeValidator(_blank_to_none)]
OptionalTime = Annotated[AwareDatetime | None, BeforeValidator(_blank_to_none)]
PositiveQty = Annotated[Decimal, Field(gt=0)]
NonNegativeQty = Annotated[Decimal, Field(ge=0)]


class SourceRow(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class PurchaseOrderLineRow(SourceRow):
    source_record_id: Text
    po_number: Text
    line_number: PositiveInt
    supplier_code: Text
    location_code: Text
    item_code: Text
    ordered_qty: PositiveQty
    ordered_uom: Text
    status: Text
    expected_receipt_by: AwareDatetime


class GoodsReceiptLineRow(SourceRow):
    source_record_id: Text
    receipt_number: Text
    receipt_line: PositiveInt
    po_number: Text
    po_line_number: PositiveInt
    location_code: Text
    product_ref: Text
    accepted_qty: NonNegativeQty
    uom: Text
    received_at: AwareDatetime
    posting_status: Text


class ProductAliasRow(SourceRow):
    source_system: Text
    external_item_id: Text
    canonical_product_id: Text
    source_uom: Text
    base_uom: Text
    to_base_factor: PositiveQty


class LocationAliasRow(SourceRow):
    source_system: Text
    external_location_id: Text
    canonical_location_id: Text
    region_id: Text


class EmployeeRow(SourceRow):
    source_employee_id: Text
    display_name: Text
    employment_status: Text
    role: Text
    location_code: OptionalText = None  # region-scoped roles have no store (D-37)
    manager_employee_id: OptionalText = None
    region_id: OptionalText = None


class WorkforceStatusRow(SourceRow):
    source_employee_id: Text
    availability_status: Text
    shift_start: OptionalTime = None
    shift_end: OptionalTime = None
    observed_at: AwareDatetime


def canonical_value(value: Any) -> Any:
    """One spelling per meaning: 100 == 100.00, and +05:30 == Z for the same instant (D-15)."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def content_hash(row: BaseModel) -> str:
    payload = {name: canonical_value(value) for name, value in row.model_dump().items()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
