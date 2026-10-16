from datetime import datetime, timezone
from decimal import Decimal

from app.mapping.resolver import (AliasIndex, LocationAlias, ProductAlias, resolve_location, resolve_po_line,
                                  resolve_receipt)

T = datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc)


def index() -> AliasIndex:
    def product(system, external, canonical, source_uom, factor):
        return ProductAlias(system, external, canonical, source_uom, "EA", Decimal(factor), "imp-p")

    def location(system, external, canonical):
        return LocationAlias(system, external, canonical, "REGION-MH", "imp-l")

    return AliasIndex(
        products={("ERP", "ERP-BOX-12"): product("ERP", "ERP-BOX-12", "SKU-002", "BOX", 12),
                  ("WMS", "WMS-MAT-2002"): product("WMS", "WMS-MAT-2002", "SKU-002", "EA", 1)},
        locations={("ERP", "MUM-015"): location("ERP", "MUM-015", "STORE-015"),
                   ("WMS", "WH-MH-015"): location("WMS", "WH-MH-015", "STORE-015"),
                   ("HRMS", "ST-015"): location("HRMS", "ST-015", "STORE-015")},
    )


def po(**overrides) -> dict:
    row = dict(source_record_id="ERP-PO505-L1", po_number="PO-505", line_number=1, location_code="MUM-015",
               item_code="ERP-BOX-12", ordered_qty=Decimal(10), ordered_uom="BOX", status="OPEN",
               expected_receipt_by=T)
    return {**row, **overrides}


def receipt(**overrides) -> dict:
    row = dict(source_record_id="WMS-GRN505-A-L1", location_code="WH-MH-015", product_ref="WMS-MAT-2002",
               accepted_qty=Decimal(96), uom="EA", received_at=T, posting_status="POSTED")
    return {**row, **overrides}


def test_box_converts_to_base_units():
    r = resolve_po_line(po(), index())
    assert r.issue is None
    assert (r.canonical_product_id, r.canonical_location_id, r.region_id, r.expected_qty, r.base_uom) == \
           ("SKU-002", "STORE-015", "REGION-MH", Decimal(120), "EA")
    assert r.mapping_refs == ("product_alias:ERP:ERP-BOX-12@imp-p", "location_alias:ERP:MUM-015@imp-l")


def test_source_facts_are_carried_through_unchanged():
    r = resolve_po_line(po(), index())
    assert (r.source_record_id, r.po_number, r.line_number, r.status, r.expected_receipt_by) == \
           ("ERP-PO505-L1", "PO-505", 1, "OPEN", T)


def test_receipt_maps_through_wms_aliases():
    r = resolve_receipt(receipt(), index())
    assert r.issue is None
    assert (r.canonical_product_id, r.canonical_location_id, r.accepted_qty, r.base_uom) == \
           ("SKU-002", "STORE-015", Decimal(96), "EA")


def test_uom_mismatch_is_an_issue_not_a_guess():
    assert resolve_po_line(po(ordered_uom="EA"), index()).issue == "UOM_MISMATCH"
    assert resolve_receipt(receipt(uom="BOX"), index()).issue == "UOM_MISMATCH"


def test_unmapped_product_and_location():
    assert resolve_po_line(po(item_code="ERP-SKU-999"), index()).issue == "UNMAPPED_PRODUCT"
    assert resolve_po_line(po(location_code="MUM-999"), index()).issue == "UNMAPPED_LOCATION"
    assert resolve_receipt(receipt(product_ref="WMS-NOPE"), index()).issue == "UNMAPPED_PRODUCT"


def test_issue_leaves_canonical_fields_empty():
    r = resolve_po_line(po(item_code="ERP-SKU-999"), index())
    assert r.canonical_product_id is None and r.expected_qty is None and r.mapping_refs == ()


def test_ids_from_another_system_do_not_match():
    # A WMS product ID on an ERP line must not resolve just because the string exists somewhere.
    assert resolve_po_line(po(item_code="WMS-MAT-2002"), index()).issue == "UNMAPPED_PRODUCT"


def test_conflicted_alias_blocks_mapping():
    idx = index()
    idx.conflicted.add(("product_aliases", "ERP|ERP-BOX-12"))
    assert resolve_po_line(po(), idx).issue == "CONFLICTED_MAPPING"
    idx = index()
    idx.conflicted.add(("location_aliases", "WMS|WH-MH-015"))
    assert resolve_receipt(receipt(), idx).issue == "CONFLICTED_MAPPING"


def test_hrms_location_resolution():
    assert resolve_location(index(), "HRMS", "ST-015").canonical_location_id == "STORE-015"
    assert resolve_location(index(), "HRMS", None) is None
    assert resolve_location(index(), "HRMS", "ST-999") is None
