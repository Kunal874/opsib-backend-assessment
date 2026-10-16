from decimal import Decimal

from app.ingestion.importer import import_csv
from app.mapping.resolver import load_alias_index, resolve_location, resolve_po_line, resolve_receipt
from tests.conftest import load_fixture_pack


def test_alias_index_loads_from_the_database(conn, settings):
    load_fixture_pack(conn, settings)
    idx = load_alias_index(conn, settings.tenant_id)
    assert len(idx.products) == 6 and len(idx.locations) == 6 and idx.conflicted == set()
    assert idx.products[("ERP", "ERP-BOX-12")].to_base_factor == Decimal(12)
    assert resolve_location(idx, "HRMS", "ST-014").canonical_location_id == "STORE-014"


def test_every_supplied_line_maps_cleanly(conn, settings):
    load_fixture_pack(conn, settings)
    idx = load_alias_index(conn, settings.tenant_id)
    pos = [resolve_po_line(r, idx) for r in conn.execute("SELECT * FROM purchase_order_lines")]
    receipts = [resolve_receipt(r, idx) for r in conn.execute("SELECT * FROM goods_receipt_lines")]
    assert [p.issue for p in pos + receipts] == [None] * 12
    po505 = next(p for p in pos if p.po_number == "PO-505")
    assert (po505.canonical_location_id, po505.expected_qty) == ("STORE-015", Decimal(120))


def test_alias_index_is_tenant_scoped(conn, settings):
    load_fixture_pack(conn, settings)
    idx = load_alias_index(conn, "some-other-retailer")
    assert idx.products == {} and idx.locations == {}


def test_open_alias_conflict_is_visible_to_the_resolver(conn, settings):
    load_fixture_pack(conn, settings)
    clash = ("source_system,external_item_id,canonical_product_id,source_uom,base_uom,to_base_factor\n"
             "ERP,ERP-BOX-12,SKU-002,BOX,EA,10\n").encode()
    assert import_csv(conn, settings.tenant_id, "product_aliases", clash)["counts"]["conflict"] == 1
    idx = load_alias_index(conn, settings.tenant_id)
    assert ("product_aliases", "ERP|ERP-BOX-12") in idx.conflicted
    po505 = conn.execute("SELECT * FROM purchase_order_lines WHERE po_number = 'PO-505'").fetchone()
    assert resolve_po_line(po505, idx).issue == "CONFLICTED_MAPPING"
