"""Source identifiers -> canonical product/location, and source quantities -> base units (D-18..D-21).

Everything except load_alias_index() is pure: it works on an AliasIndex snapshot, so the rules can be
unit-tested without a database. Nothing here guesses. A missing alias, a unit that doesn't match the
alias, or an alias with an open source conflict comes back as an `issue`; the line is then reported
as NOT_EVALUABLE rather than evaluated on a wrong number.

Mapping runs at evaluation time, not import time, so files can arrive in any order (D-18).
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import psycopg

MAPPING_VERSION = "mapping/v1"


@dataclass(frozen=True)
class ProductAlias:
    source_system: str
    external_item_id: str
    canonical_product_id: str
    source_uom: str
    base_uom: str
    to_base_factor: Decimal
    import_id: str

    @property
    def ref(self) -> str:
        return f"product_alias:{self.source_system}:{self.external_item_id}@{self.import_id}"


@dataclass(frozen=True)
class LocationAlias:
    source_system: str
    external_location_id: str
    canonical_location_id: str
    region_id: str
    import_id: str

    @property
    def ref(self) -> str:
        return f"location_alias:{self.source_system}:{self.external_location_id}@{self.import_id}"


@dataclass
class AliasIndex:
    products: dict[tuple[str, str], ProductAlias] = field(default_factory=dict)
    locations: dict[tuple[str, str], LocationAlias] = field(default_factory=dict)
    conflicted: set[tuple[str, str]] = field(default_factory=set)  # (dataset, source_key) with an OPEN conflict


@dataclass(frozen=True)
class ResolvedPoLine:
    source_record_id: str
    po_number: str
    line_number: int
    status: str
    expected_receipt_by: datetime
    canonical_product_id: str | None = None
    canonical_location_id: str | None = None
    region_id: str | None = None
    expected_qty: Decimal | None = None  # in base_uom
    base_uom: str | None = None
    ordered_qty: Decimal | None = None   # as the source sent it, e.g. 10 ...
    ordered_uom: str | None = None       # ... BOX — kept for explanations
    mapping_refs: tuple[str, ...] = ()
    issue: str | None = None  # set => canonical fields are None


@dataclass(frozen=True)
class ResolvedReceipt:
    source_record_id: str
    received_at: datetime
    posting_status: str
    canonical_product_id: str | None = None
    canonical_location_id: str | None = None
    accepted_qty: Decimal | None = None  # in base_uom
    base_uom: str | None = None
    mapping_refs: tuple[str, ...] = ()
    issue: str | None = None


class _Issue(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def load_alias_index(conn: psycopg.Connection, tenant_id: str) -> AliasIndex:
    products = {
        (r["source_system"], r["external_item_id"]): ProductAlias(
            r["source_system"], r["external_item_id"], r["canonical_product_id"], r["source_uom"],
            r["base_uom"], r["to_base_factor"], str(r["import_id"]))
        for r in conn.execute("SELECT * FROM product_aliases WHERE tenant_id = %s", (tenant_id,))
    }
    locations = {
        (r["source_system"], r["external_location_id"]): LocationAlias(
            r["source_system"], r["external_location_id"], r["canonical_location_id"], r["region_id"],
            str(r["import_id"]))
        for r in conn.execute("SELECT * FROM location_aliases WHERE tenant_id = %s", (tenant_id,))
    }
    conflicted = {
        (r["dataset"], r["source_key"])
        for r in conn.execute(
            "SELECT dataset, source_key FROM source_conflicts WHERE tenant_id = %s AND status = 'OPEN'",
            (tenant_id,))
    }
    return AliasIndex(products, locations, conflicted)


def resolve_location(index: AliasIndex, source_system: str, code: str | None) -> LocationAlias | None:
    """Used for employee (HRMS) locations: None when there is no usable mapping."""
    if code is None:
        return None
    try:
        return _location(index, source_system, code)
    except _Issue:
        return None


def resolve_po_line(row: Mapping, index: AliasIndex) -> ResolvedPoLine:
    facts = dict(source_record_id=row["source_record_id"], po_number=row["po_number"],
                 line_number=row["line_number"], status=row["status"],
                 expected_receipt_by=row["expected_receipt_by"],
                 ordered_qty=row["ordered_qty"], ordered_uom=row["ordered_uom"])
    system = row.get("source_system", "ERP")
    try:
        location = _location(index, system, row["location_code"])
        product = _product(index, system, row["item_code"])
        expected = _to_base(row["ordered_qty"], row["ordered_uom"], product)
    except _Issue as issue:
        return ResolvedPoLine(**facts, issue=issue.code)
    return ResolvedPoLine(
        **facts, canonical_product_id=product.canonical_product_id,
        canonical_location_id=location.canonical_location_id, region_id=location.region_id,
        expected_qty=expected, base_uom=product.base_uom, mapping_refs=(product.ref, location.ref))


def resolve_receipt(row: Mapping, index: AliasIndex) -> ResolvedReceipt:
    facts = dict(source_record_id=row["source_record_id"], received_at=row["received_at"],
                 posting_status=row["posting_status"])
    system = row.get("source_system", "WMS")
    try:
        location = _location(index, system, row["location_code"])
        product = _product(index, system, row["product_ref"])
        accepted = _to_base(row["accepted_qty"], row["uom"], product)
    except _Issue as issue:
        return ResolvedReceipt(**facts, issue=issue.code)
    return ResolvedReceipt(
        **facts, canonical_product_id=product.canonical_product_id,
        canonical_location_id=location.canonical_location_id, accepted_qty=accepted,
        base_uom=product.base_uom, mapping_refs=(product.ref, location.ref))


# Alias source keys use the same "system|external_id" format as DatasetSpec.source_key().

def _location(index: AliasIndex, system: str, code: str) -> LocationAlias:
    alias = index.locations.get((system, code))
    if alias is None:
        raise _Issue("UNMAPPED_LOCATION")
    if ("location_aliases", f"{system}|{code}") in index.conflicted:
        raise _Issue("CONFLICTED_MAPPING")
    return alias


def _product(index: AliasIndex, system: str, external_id: str) -> ProductAlias:
    alias = index.products.get((system, external_id))
    if alias is None:
        raise _Issue("UNMAPPED_PRODUCT")
    if ("product_aliases", f"{system}|{external_id}") in index.conflicted:
        raise _Issue("CONFLICTED_MAPPING")
    return alias


def _to_base(quantity: Decimal, uom: str, alias: ProductAlias) -> Decimal:
    # The factor belongs to the alias's source unit; any other unit would need a conversion we don't have.
    if uom != alias.source_uom:
        raise _Issue("UOM_MISMATCH")
    return quantity * alias.to_base_factor
