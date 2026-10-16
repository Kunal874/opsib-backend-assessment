"""Reading exceptions back out, with lineage down to the original CSV line (provenance, D-11 / D-19)."""
import uuid
from decimal import Decimal

import psycopg

from app.audit import history
from app.detection.findings import fmt_qty
from app.errors import AppError

COLUMNS = """
exception_id, exception_type, business_key, status, rule_version, po_number, po_line_number,
canonical_product_id, canonical_location_id, expected_qty::text AS expected_qty, received_qty::text AS received_qty,
variance_qty::text AS variance_qty, uom, evaluated_as_of, first_detected_at, last_detected_at
"""

# Each source fact joined to the raw CSV line that first delivered it (same import, ACCEPTED outcome).
SOURCE_SQL = """
SELECT f.*, r.row_number AS source_row_number, r.raw AS raw_row
  FROM {table} f
  LEFT JOIN import_rows r ON r.import_id = f.import_id AND r.source_key = f.source_record_id AND r.outcome = 'ACCEPTED'
 WHERE f.tenant_id = %s AND f.source_record_id = ANY(%s)
 ORDER BY f.source_record_id
"""


def list_exceptions(conn: psycopg.Connection, tenant_id: str, status: str | None = None,
                    location_id: str | None = None) -> list[dict]:
    return conn.execute(
        f"SELECT {COLUMNS} FROM exceptions WHERE tenant_id = %(t)s"
        " AND (%(s)s::text IS NULL OR status = %(s)s)"
        " AND (%(l)s::text IS NULL OR canonical_location_id = %(l)s)"
        " ORDER BY po_number, po_line_number",
        {"t": tenant_id, "s": status, "l": location_id}).fetchall()


def get_exception(conn: psycopg.Connection, tenant_id: str, exception_id: uuid.UUID | str) -> dict:
    row = conn.execute(
        f"SELECT {COLUMNS}, evidence, explanation, unknowns, resolution FROM exceptions"
        " WHERE tenant_id = %s AND exception_id = %s", (tenant_id, exception_id)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", f"Exception {exception_id} not found")
    evidence = row["evidence"]
    row["source_records"] = {
        "purchase_order_lines": _sources(conn, tenant_id, "purchase_order_lines", evidence["po_source_ids"]),
        "goods_receipt_lines": _sources(conn, tenant_id, "goods_receipt_lines", evidence["receipt_source_ids"]),
    }
    row["tasks"] = conn.execute(
        "SELECT task_id, task_type, status, assignee_employee_id, due_at FROM tasks"
        " WHERE tenant_id = %s AND exception_id = %s ORDER BY task_type", (tenant_id, exception_id)).fetchall()
    row["history"] = history(conn, tenant_id, "exception", exception_id)
    return row


def _sources(conn: psycopg.Connection, tenant_id: str, table: str, source_ids: list[str]) -> list[dict]:
    rows = conn.execute(SOURCE_SQL.format(table=table), (tenant_id, source_ids)).fetchall()
    # Quantities as strings, like every other API response (D-47).
    return [{k: fmt_qty(v) if isinstance(v, Decimal) else v for k, v in r.items() if k != "content_hash"}
            for r in rows]
