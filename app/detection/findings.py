"""From an evaluation to a stored finding ("exception"), idempotent by business key (D-29..D-32).

build_finding() is pure. upsert_finding() / close_if_open() are the only writers of the exceptions table
during detection, and they write an audit event only when something actually changed — so re-running
detection on unchanged data is a no-op.
"""
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import psycopg

from app.audit import record_event
from app.db import to_jsonb
from app.detection.shortfall import RULE_VERSION, Evaluation
from app.mapping.resolver import MAPPING_VERSION, ResolvedPoLine

EXCEPTION_TYPE = "RECEIPT_SHORTFALL"
ACTOR = "system:detection"
# The fixtures contain no dispatch/ASN data, so the cause of a gap is never asserted (D-32).
UNKNOWNS = (
    "Supplier dispatch/ASN quantity was not provided; cannot tell whether fewer units were shipped.",
    "Carrier proof of delivery was not provided; cannot tell whether units were lost in transit.",
    "No physical recount at the receiving location; cannot rule out a receiving or counting error.",
)
# Columns a re-run may legitimately change; anything else is identity.
COMPARED = ("rule_version", "canonical_product_id", "canonical_location_id", "expected_qty", "received_qty",
            "variance_qty", "uom", "evidence", "explanation", "unknowns")


def business_key(po_number: str, line_number: int) -> str:
    return f"{EXCEPTION_TYPE}|{po_number}|{line_number}"


def exception_id(tenant_id: str, key: str) -> uuid.UUID:
    # Deterministic: the same business problem gets the same ID on every run, in every database.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"opsib:{tenant_id}|{key}")


def fmt_qty(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def build_finding(tenant_id: str, po: ResolvedPoLine, ev: Evaluation, as_of: datetime,
                  receipt_mapping_refs: Sequence[str]) -> dict:
    key = business_key(po.po_number, po.line_number)
    return {
        "exception_id": exception_id(tenant_id, key),
        "tenant_id": tenant_id,
        "exception_type": EXCEPTION_TYPE,
        "business_key": key,
        "rule_version": RULE_VERSION,
        "po_number": po.po_number,
        "po_line_number": po.line_number,
        "canonical_product_id": po.canonical_product_id,
        "canonical_location_id": po.canonical_location_id,
        "expected_qty": ev.expected_qty,
        "received_qty": ev.received_qty,
        "variance_qty": ev.variance_qty,
        "uom": po.base_uom,
        "evidence": {
            "po_source_ids": [po.source_record_id],
            "receipt_source_ids": list(ev.counted_receipt_ids),
            "ignored_receipts": [{"source_record_id": rid, "reason": why} for rid, why in ev.ignored_receipts],
            "mapping_refs": sorted({*po.mapping_refs, *receipt_mapping_refs}),
            "mapping_version": MAPPING_VERSION,
        },
        "explanation": _explanation(po, ev, as_of),
        "unknowns": list(UNKNOWNS),
        "evaluated_as_of": as_of,
    }


def _explanation(po: ResolvedPoLine, ev: Evaluation, as_of: datetime) -> str:
    # Facts only: what was ordered, what was accepted, by when. No claim about why.
    uom = po.base_uom
    conversion = ""
    if po.ordered_uom and po.ordered_uom != uom:
        factor = fmt_qty(po.expected_qty / po.ordered_qty)
        conversion = f" ({fmt_qty(po.ordered_qty)} {po.ordered_uom} × {factor})"
    deadline = po.expected_receipt_by.astimezone(as_of.tzinfo).isoformat()
    return (
        f"After the expected-receipt deadline ({deadline}), posted receipts for {po.po_number} line "
        f"{po.line_number} total {fmt_qty(ev.received_qty)} {uom} against {fmt_qty(ev.expected_qty)} {uom} "
        f"ordered{conversion}. Receipt shortfall: {fmt_qty(ev.variance_qty)} {uom}. "
        f"The available data does not establish the cause."
    )


def upsert_finding(conn: psycopg.Connection, finding: dict, run_id: uuid.UUID) -> str:
    """Returns CREATED | UPDATED | UNCHANGED | REOPENED. Call inside the detection transaction."""
    columns = list(finding)
    # No conflict target on purpose: exception_id (PK) and the business key are two unique constraints for
    # the same identity. A targeted ON CONFLICT only absorbs its own constraint, so a concurrent run could
    # still fail on the other one (found by test_concurrent_detection_creates_each_exception_once).
    inserted = conn.execute(
        f"INSERT INTO exceptions ({', '.join(columns)}, status) VALUES ({', '.join(['%s'] * len(columns))}, 'OPEN')"
        " ON CONFLICT DO NOTHING RETURNING exception_id",
        [_param(finding[c]) for c in columns],
    ).fetchone()
    if inserted:
        _audit(conn, finding, "FINDING_CREATED", None, "OPEN",
               {"run_id": run_id, "variance_qty": finding["variance_qty"], "uom": finding["uom"]})
        return "CREATED"

    # Someone (maybe a concurrent run) created it already: lock it and compare.
    current = conn.execute("SELECT * FROM exceptions WHERE exception_id = %s FOR UPDATE",
                           (finding["exception_id"],)).fetchone()
    if current["status"] == "RESOLVED":
        return "UNCHANGED"  # a verified resolution is final; new evidence would be a new case (D-30)
    changes = {c: [current[c], finding[c]] for c in COMPARED if current[c] != finding[c]}
    if current["status"] == "OPEN" and not changes:
        return "UNCHANGED"

    assignments = ", ".join(f"{c} = %s" for c in COMPARED)
    conn.execute(
        f"UPDATE exceptions SET {assignments}, evaluated_as_of = %s, status = 'OPEN', last_detected_at = now()"
        " WHERE exception_id = %s",
        [*(_param(finding[c]) for c in COMPARED), finding["evaluated_as_of"], finding["exception_id"]],
    )
    reopened = current["status"] == "CLOSED"
    _audit(conn, finding, "FINDING_REOPENED" if reopened else "FINDING_UPDATED", current["status"], "OPEN",
           {"run_id": run_id, "changes": changes})
    return "REOPENED" if reopened else "UPDATED"


def close_if_open(conn: psycopg.Connection, tenant_id: str, key: str, ev: Evaluation, run_id: uuid.UUID) -> bool:
    """The shortfall is gone (e.g. a late receipt arrived): close the case with the evidence for why."""
    closed = conn.execute(
        "UPDATE exceptions SET status = 'CLOSED', received_qty = %s, variance_qty = %s, last_detected_at = now()"
        " WHERE tenant_id = %s AND exception_type = %s AND business_key = %s AND status = 'OPEN'"
        " RETURNING exception_id",
        (ev.received_qty, ev.variance_qty, tenant_id, EXCEPTION_TYPE, key),
    ).fetchone()
    if closed:
        record_event(conn, tenant_id, "exception", closed["exception_id"], "FINDING_CLOSED", ACTOR,
                     from_status="OPEN", to_status="CLOSED",
                     data={"run_id": run_id, "reason": "Shortfall no longer present after re-evaluation",
                           "received_qty": ev.received_qty, "variance_qty": ev.variance_qty,
                           "receipt_source_ids": list(ev.counted_receipt_ids)})
    return closed is not None


def _param(value):
    return to_jsonb(value) if isinstance(value, (dict, list)) else value


def _audit(conn, finding: dict, event_type: str, from_status: str | None, to_status: str, data: dict) -> None:
    record_event(conn, finding["tenant_id"], "exception", finding["exception_id"], event_type, ACTOR,
                 from_status=from_status, to_status=to_status, data=data)
