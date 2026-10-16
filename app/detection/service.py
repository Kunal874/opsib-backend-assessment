"""One detection run: load source facts -> resolve -> evaluate every PO line -> persist findings.

The whole run is one transaction (D-10). PO lines are processed in business-key order, so concurrent
runs touch exceptions rows in the same order: a second run waits on the first run's unique key instead
of deadlocking, then finds the case already there and leaves it unchanged (D-29, D-34).
"""
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from itertools import groupby

import psycopg

from app.config import Settings
from app.db import to_jsonb
from app.detection.findings import build_finding, business_key, close_if_open, fmt_qty, upsert_finding
from app.detection.shortfall import RULE_VERSION, Evaluation, Outcome, evaluate_shortfall
from app.errors import AppError
from app.log import log
from app.mapping.resolver import AliasIndex, load_alias_index, resolve_po_line, resolve_receipt

CHANGE_KINDS = ("created", "updated", "unchanged", "reopened", "closed")


def run_detection(conn: psycopg.Connection, settings: Settings, as_of: datetime | None = None) -> dict:
    as_of = as_of or settings.evaluation_time
    if as_of.tzinfo is None:
        raise AppError(422, "VALIDATION_ERROR", "as_of must include a UTC offset")
    tenant_id, run_id = settings.tenant_id, uuid.uuid4()
    outcomes, changes, lines = Counter(), Counter(), []

    with conn.transaction():
        index = load_alias_index(conn, tenant_id)
        po_rows = conn.execute(
            "SELECT * FROM purchase_order_lines WHERE tenant_id = %s ORDER BY po_number, line_number, source_record_id",
            (tenant_id,)).fetchall()
        receipts_by_line = defaultdict(list)
        for receipt in conn.execute("SELECT * FROM goods_receipt_lines WHERE tenant_id = %s", (tenant_id,)):
            receipts_by_line[(receipt["po_number"], receipt["po_line_number"])].append(receipt)

        for (po_number, line_number), group in groupby(po_rows, key=lambda r: (r["po_number"], r["line_number"])):
            group = list(group)
            receipt_rows = receipts_by_line.get((po_number, line_number), [])
            po = resolve_po_line(group[0], index)
            receipts = [resolve_receipt(r, index) for r in receipt_rows]
            ev = evaluate_shortfall(po, receipts, as_of, settings.shortfall_tolerance,
                                    blocked_by=_blocked_reason(group, receipt_rows, index))
            finding_id = None
            if ev.outcome == Outcome.SHORTFALL:
                counted = set(ev.counted_receipt_ids)
                refs = [ref for r in receipts if r.source_record_id in counted for ref in r.mapping_refs]
                finding = build_finding(tenant_id, po, ev, as_of, refs)
                changes[upsert_finding(conn, finding, run_id).lower()] += 1
                finding_id = str(finding["exception_id"])
                # Task 7 hook: route a task for new / reopened findings.
            elif ev.outcome == Outcome.NO_SHORTFALL:
                changes["closed"] += close_if_open(conn, tenant_id, business_key(po_number, line_number), ev, run_id)
            outcomes[ev.outcome] += 1
            lines.append(_line(po_number, line_number, ev, finding_id))

        result = {
            "run_id": str(run_id),
            "as_of": as_of.isoformat(),
            "rule_version": RULE_VERSION,
            "counts": {o.value: outcomes[o] for o in Outcome},
            "exceptions": {k: changes[k] for k in CHANGE_KINDS},
            "tasks_created": 0,
            "lines": lines,
        }
        conn.execute(
            "INSERT INTO detection_runs (run_id, tenant_id, rule_version, as_of, summary, line_outcomes)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (run_id, tenant_id, RULE_VERSION, as_of,
             to_jsonb({k: result[k] for k in ("counts", "exceptions", "tasks_created")}), to_jsonb(lines)),
        )

    log.info("detection_finished", extra={"fields": {"run_id": str(run_id), "as_of": as_of.isoformat(),
                                                     **result["counts"], **result["exceptions"]}})
    return result


def _blocked_reason(po_rows: list[dict], receipt_rows: list[dict], index: AliasIndex) -> str | None:
    if len(po_rows) > 1:
        return "AMBIGUOUS_PO_LINE"  # two source records claim the same PO line (e.g. re-keyed in the ERP) (D-22)
    keys = [("purchase_order_lines", po_rows[0]["source_record_id"])]
    keys += [("goods_receipt_lines", r["source_record_id"]) for r in receipt_rows]
    if any(key in index.conflicted for key in keys):
        return "OPEN_SOURCE_CONFLICT"  # disputed data is not evaluated (D-16)
    return None


def _line(po_number: str, line_number: int, ev: Evaluation, exception_id: str | None) -> dict:
    return {
        "po_number": po_number,
        "line_number": line_number,
        "outcome": ev.outcome.value,
        "reason": ev.reason,
        "expected_qty": fmt_qty(ev.expected_qty),
        "received_qty": fmt_qty(ev.received_qty),
        "variance_qty": fmt_qty(ev.variance_qty),
        "counted_receipt_ids": list(ev.counted_receipt_ids),
        "exception_id": exception_id,
    }
