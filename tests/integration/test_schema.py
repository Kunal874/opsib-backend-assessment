"""The schema itself enforces the business invariants — these tests prove the constraints exist."""
import uuid

import psycopg
import pytest

PO_INSERT = (
    "INSERT INTO purchase_order_lines (tenant_id, source_record_id, po_number, line_number, supplier_code,"
    " location_code, item_code, ordered_qty, ordered_uom, status, expected_receipt_by, content_hash, import_id)"
    " VALUES (%s, %s, 'PO-1', 1, 'S', 'L', 'I', %s, 'EA', 'OPEN', '2026-10-15T12:00:00+05:30', 'h', %s)"
)
EXCEPTION_INSERT = (
    "INSERT INTO exceptions (exception_id, tenant_id, exception_type, business_key, status, rule_version,"
    " po_number, po_line_number, canonical_product_id, canonical_location_id, expected_qty, received_qty,"
    " variance_qty, uom, evidence, explanation, unknowns, evaluated_as_of)"
    " VALUES (%s, 't1', 'RECEIPT_SHORTFALL', %s, 'OPEN', 'v1', 'PO-1', 1, 'SKU', 'STORE', 10, 2, 8, 'EA',"
    " '{}', 'e', '[]', now())"
)
TASK_INSERT = (
    "INSERT INTO tasks (task_id, tenant_id, exception_id, task_type, status, canonical_location_id, due_at,"
    " assignee_employee_id) VALUES (%s, 't1', %s, 'T', %s, 'STORE', now(), %s)"
)


def _import_run(conn) -> uuid.UUID:
    import_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO import_runs (import_id, tenant_id, dataset, file_sha256, status)"
        " VALUES (%s, 't1', 'purchase_order_lines', 'x', 'PROCESSING')",
        (import_id,),
    )
    return import_id


def _exception(conn, key="k") -> uuid.UUID:
    exception_id = uuid.uuid4()
    conn.execute(EXCEPTION_INSERT, (exception_id, key))
    return exception_id


def test_source_identity_is_unique_per_tenant(conn):
    import_id = _import_run(conn)
    conn.execute(PO_INSERT, ("t1", "ERP-1", 10, import_id))
    conn.execute(PO_INSERT, ("t2", "ERP-1", 10, import_id))  # same source ID, other tenant: allowed
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(PO_INSERT, ("t1", "ERP-1", 10, import_id))


def test_ordered_qty_must_be_positive(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(PO_INSERT, ("t1", "ERP-2", 0, _import_run(conn)))


def test_one_exception_per_business_key(conn):
    _exception(conn, "RECEIPT_SHORTFALL|PO-1|1")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _exception(conn, "RECEIPT_SHORTFALL|PO-1|1")


def test_one_task_per_exception_and_type(conn):
    exception_id = _exception(conn)
    conn.execute(TASK_INSERT, (uuid.uuid4(), exception_id, "CREATED", None))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(TASK_INSERT, (uuid.uuid4(), exception_id, "CREATED", None))


@pytest.mark.parametrize("status", ["ASSIGNED", "IN_PROGRESS", "VERIFIED"])
def test_task_past_created_needs_assignee(conn, status):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(TASK_INSERT, (uuid.uuid4(), _exception(conn), status, None))


def test_unknown_task_status_is_rejected(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(TASK_INSERT, (uuid.uuid4(), _exception(conn), "DONE", "HR-102"))


def test_conflict_for_same_incoming_version_is_recorded_once(conn):
    import_id = _import_run(conn)
    insert = (
        "INSERT INTO source_conflicts (tenant_id, dataset, source_key, existing_hash, incoming_hash, incoming_row,"
        " import_id, row_number) VALUES ('t1', 'purchase_order_lines', 'ERP-1', 'a', 'b', '{}', %s, 2)"
    )
    conn.execute(insert, (import_id,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (import_id,))
