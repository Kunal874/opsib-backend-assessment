import threading

import pytest

from app.db import connect
from app.ingestion.importer import import_csv
from tests.conftest import DATA, FIXTURES, load_fixture_pack

ZERO = {"conflict": 0, "rejected": 0}


def count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()["n"]


def test_fixture_pack_counts(conn, settings):
    reports = load_fixture_pack(conn, settings)
    c = {name: r["counts"] for name, r in reports.items()}
    assert c["purchase_order_lines"] == {"total": 7, "accepted": 6, "duplicate": 1, **ZERO}
    assert c["goods_receipt_lines"] == {"total": 7, "accepted": 6, "duplicate": 1, **ZERO}
    for name in ("product_aliases", "location_aliases", "employees", "workforce_status"):
        assert c[name] == {"total": 6, "accepted": 6, "duplicate": 0, **ZERO}
    assert all(r["status"] == "COMPLETED" for r in reports.values())
    dup = [r for r in reports["purchase_order_lines"]["rows"] if r["outcome"] == "DUPLICATE"]
    assert dup == [{"row_number": 8, "source_key": "ERP-PO506-L1", "outcome": "DUPLICATE", "errors": []}]


def test_reimport_is_all_duplicates_and_changes_nothing(conn, settings):
    load_fixture_pack(conn, settings)
    again = load_fixture_pack(conn, settings)
    assert again["goods_receipt_lines"]["counts"] == {"total": 7, "accepted": 0, "duplicate": 7, **ZERO}
    assert count(conn, "SELECT count(*) AS n FROM goods_receipt_lines") == 6
    assert count(conn, "SELECT count(*) AS n FROM purchase_order_lines") == 6
    assert count(conn, "SELECT count(*) AS n FROM import_runs") == 12          # both attempts stay visible


def test_bad_rows_are_rejected_with_reasons_and_good_rows_load(conn, settings):
    r = import_csv(conn, settings.tenant_id, "purchase_order_lines", (DATA / "po_bad_rows.csv").read_bytes())
    assert r["status"] == "COMPLETED"
    assert r["counts"] == {"total": 4, "accepted": 1, "duplicate": 0, "conflict": 0, "rejected": 3}
    reasons = {row["row_number"]: " ".join(row["errors"]) for row in r["rows"]}
    assert "ordered_qty" in reasons[2]
    assert "expected_receipt_by" in reasons[3] and "timezone" in reasons[3]
    assert "po_number" in reasons[4]
    raw = conn.execute("SELECT raw FROM import_rows WHERE import_id=%s AND row_number=3", (r["import_id"],)).fetchone()
    assert raw["raw"]["expected_receipt_by"] == "2026-10-15T12:00:00"          # raw text kept exactly
    assert count(conn, "SELECT count(*) AS n FROM purchase_order_lines") == 1


def test_missing_column_fails_the_import_visibly(client, conn):
    r = client.post("/imports/purchase_order_lines", content=(DATA / "po_missing_column.csv").read_bytes(),
                    headers={"Content-Type": "text/csv"})
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "BAD_CSV" and "expected_receipt_by" in error["message"]
    report = client.get(f"/imports/{error['details']['import_id']}").json()
    assert report["status"] == "FAILED"
    assert count(conn, "SELECT count(*) AS n FROM import_rows") == 0             # nothing half-applied


def test_import_report_via_api(client):
    created = client.post("/imports/purchase_order_lines?source_filename=po.csv",
                          content=(FIXTURES / "purchase_order_lines.csv").read_bytes(),
                          headers={"Content-Type": "text/csv"})
    assert created.status_code == 201
    report = client.get(f"/imports/{created.json()['import_id']}").json()
    assert report["source_filename"] == "po.csv" and report["counts"]["duplicate"] == 1


def test_conflicting_duplicate_keeps_original_and_records_conflict(conn, settings):
    load_fixture_pack(conn, settings)
    content = (DATA / "po_conflicting_duplicate.csv").read_bytes()
    r = import_csv(conn, settings.tenant_id, "purchase_order_lines", content)
    assert r["counts"]["conflict"] == 1
    assert r["rows"] == [{"row_number": 2, "source_key": "ERP-PO501-L1", "outcome": "CONFLICT", "errors": []}]
    qty = conn.execute("SELECT ordered_qty FROM purchase_order_lines WHERE source_record_id='ERP-PO501-L1'").fetchone()
    assert qty["ordered_qty"] == 100                                            # not overwritten
    import_csv(conn, settings.tenant_id, "purchase_order_lines", content)
    assert count(conn, "SELECT count(*) AS n FROM source_conflicts WHERE status='OPEN'") == 1


def test_concurrent_imports_of_same_file_store_each_fact_once(conn, settings):
    content = (FIXTURES / "purchase_order_lines.csv").read_bytes()
    reports, barrier = [], threading.Barrier(2)

    def worker():
        with connect(settings.database_url) as c:
            barrier.wait()
            reports.append(import_csv(c, settings.tenant_id, "purchase_order_lines", content))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r["counts"]["accepted"] for r in reports) == 6
    assert sum(r["counts"]["duplicate"] for r in reports) == 8
    assert count(conn, "SELECT count(*) AS n FROM purchase_order_lines") == 6


def test_crash_mid_import_rolls_back_rows_and_marks_failed(conn, settings, monkeypatch):
    import app.ingestion.importer as importer

    real, calls = importer.content_hash, {"n": 0}

    def flaky(row):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk on fire")
        return real(row)

    monkeypatch.setattr(importer, "content_hash", flaky)
    with pytest.raises(RuntimeError):
        import_csv(conn, settings.tenant_id, "purchase_order_lines", (FIXTURES / "purchase_order_lines.csv").read_bytes())
    run = conn.execute("SELECT status, error FROM import_runs").fetchone()
    assert run["status"] == "FAILED" and "RuntimeError" in run["error"]
    assert count(conn, "SELECT count(*) AS n FROM purchase_order_lines") == 0
    assert count(conn, "SELECT count(*) AS n FROM import_rows") == 0


def test_unknown_dataset_is_404(client):
    r = client.post("/imports/nonsense", content=b"a,b\n1,2\n", headers={"Content-Type": "text/csv"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "UNKNOWN_DATASET"


def test_non_utf8_file_is_a_bad_csv(client):
    r = client.post("/imports/employees", content=b"\xff\xfe\x00bad", headers={"Content-Type": "text/csv"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "BAD_CSV"
