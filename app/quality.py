"""The data-quality view: everything the system refused to guess about, in one place (D-21, D-31).

  rejected_rows        rows that failed validation, with reasons and the raw text
  open_conflicts       same source ID arrived with different content; original kept
  unmapped_references  source IDs with no alias (PO/receipt products + locations, employee stores)
  not_evaluable_lines  PO lines the latest detection run could not evaluate, and why
"""
import psycopg

# ponytail: unbounded lists; add paging (limit/after) once a tenant has thousands of bad rows.

UNMAPPED_SQL = """
SELECT 'purchase_order_lines' AS dataset, p.source_record_id AS source_key, 'location_code' AS field,
       p.location_code AS value
  FROM purchase_order_lines p
  LEFT JOIN location_aliases a ON a.tenant_id = p.tenant_id AND a.source_system = p.source_system
                              AND a.external_location_id = p.location_code
 WHERE p.tenant_id = %(t)s AND a.external_location_id IS NULL
UNION ALL
SELECT 'purchase_order_lines', p.source_record_id, 'item_code', p.item_code
  FROM purchase_order_lines p
  LEFT JOIN product_aliases a ON a.tenant_id = p.tenant_id AND a.source_system = p.source_system
                             AND a.external_item_id = p.item_code
 WHERE p.tenant_id = %(t)s AND a.external_item_id IS NULL
UNION ALL
SELECT 'goods_receipt_lines', g.source_record_id, 'location_code', g.location_code
  FROM goods_receipt_lines g
  LEFT JOIN location_aliases a ON a.tenant_id = g.tenant_id AND a.source_system = g.source_system
                              AND a.external_location_id = g.location_code
 WHERE g.tenant_id = %(t)s AND a.external_location_id IS NULL
UNION ALL
SELECT 'goods_receipt_lines', g.source_record_id, 'product_ref', g.product_ref
  FROM goods_receipt_lines g
  LEFT JOIN product_aliases a ON a.tenant_id = g.tenant_id AND a.source_system = g.source_system
                             AND a.external_item_id = g.product_ref
 WHERE g.tenant_id = %(t)s AND a.external_item_id IS NULL
UNION ALL
SELECT 'employees', e.source_employee_id, 'location_code', e.location_code
  FROM employees e
  LEFT JOIN location_aliases a ON a.tenant_id = e.tenant_id AND a.source_system = e.source_system
                              AND a.external_location_id = e.location_code
 WHERE e.tenant_id = %(t)s AND e.location_code IS NOT NULL AND a.external_location_id IS NULL
ORDER BY 1, 2, 3
"""


def data_quality_report(conn: psycopg.Connection, tenant_id: str) -> dict:
    rejected = conn.execute(
        "SELECT r.import_id, i.dataset, r.row_number, r.errors, r.raw FROM import_rows r"
        " JOIN import_runs i USING (import_id)"
        " WHERE i.tenant_id = %s AND r.outcome = 'REJECTED' ORDER BY i.started_at, r.row_number",
        (tenant_id,)).fetchall()
    conflicts = conn.execute(
        "SELECT conflict_id, dataset, source_key, import_id, row_number, detected_at FROM source_conflicts"
        " WHERE tenant_id = %s AND status = 'OPEN' ORDER BY conflict_id",
        (tenant_id,)).fetchall()
    latest_run = conn.execute(
        "SELECT run_id, as_of, line_outcomes FROM detection_runs WHERE tenant_id = %s"
        " ORDER BY started_at DESC LIMIT 1",
        (tenant_id,)).fetchone()
    not_evaluable = [] if latest_run is None else [
        {"po_number": line["po_number"], "line_number": line["line_number"], "reason": line["reason"]}
        for line in latest_run["line_outcomes"] if line["outcome"] == "NOT_EVALUABLE"
    ]
    return {
        "rejected_rows": rejected,
        "open_conflicts": conflicts,
        "unmapped_references": conn.execute(UNMAPPED_SQL, {"t": tenant_id}).fetchall(),
        "not_evaluable_lines": not_evaluable,
        "latest_detection_run": None if latest_run is None else
        {"run_id": str(latest_run["run_id"]), "as_of": latest_run["as_of"]},
    }
