"""Bad, unmapped and conflicting data is visible in GET /data-quality — never silently dropped or guessed."""
from app.detection.service import run_detection
from app.ingestion.importer import import_csv
from tests.conftest import DATA, load_fixture_pack


def test_clean_fixtures_have_no_data_quality_issues(client, conn, settings):
    load_fixture_pack(conn, settings)
    run_detection(conn, settings)
    dq = client.get("/data-quality").json()
    assert dq["rejected_rows"] == [] and dq["open_conflicts"] == []
    assert dq["unmapped_references"] == [] and dq["not_evaluable_lines"] == []


def test_unknown_product_is_a_data_quality_issue_not_an_exception(client, conn, settings):
    load_fixture_pack(conn, settings)
    import_csv(conn, settings.tenant_id, "purchase_order_lines", (DATA / "po_unknown_product.csv").read_bytes())
    run = run_detection(conn, settings)
    line = next(l for l in run["lines"] if l["po_number"] == "PO-507")
    assert (line["outcome"], line["reason"]) == ("NOT_EVALUABLE", "UNMAPPED_PRODUCT")
    assert not conn.execute("SELECT 1 FROM exceptions WHERE po_number = 'PO-507'").fetchone()
    dq = client.get("/data-quality").json()
    assert dq["unmapped_references"] == [{"dataset": "purchase_order_lines", "source_key": "ERP-PO507-L1",
                                          "field": "item_code", "value": "ERP-SKU-999"}]
    assert dq["not_evaluable_lines"] == [{"po_number": "PO-507", "line_number": 1, "reason": "UNMAPPED_PRODUCT"}]


def test_conflicting_duplicate_blocks_evaluation(client, conn, settings):
    load_fixture_pack(conn, settings)
    import_csv(conn, settings.tenant_id, "purchase_order_lines", (DATA / "po_conflicting_duplicate.csv").read_bytes())
    run = run_detection(conn, settings)
    line = next(l for l in run["lines"] if l["po_number"] == "PO-501")
    assert (line["outcome"], line["reason"]) == ("NOT_EVALUABLE", "OPEN_SOURCE_CONFLICT")
    assert not conn.execute("SELECT 1 FROM exceptions WHERE po_number = 'PO-501'").fetchone()
    conflicts = client.get("/data-quality").json()["open_conflicts"]
    assert [(c["dataset"], c["source_key"]) for c in conflicts] == [("purchase_order_lines", "ERP-PO501-L1")]


def test_two_source_records_for_one_po_line_are_ambiguous(conn, settings):
    load_fixture_pack(conn, settings)
    rekeyed = ("source_record_id,po_number,line_number,supplier_code,location_code,item_code,ordered_qty,"
               "ordered_uom,status,expected_receipt_by\n"
               "ERP-PO502-L1-V2,PO-502,1,SUP-07,MUM-014,ERP-SKU-001,40,EA,OPEN,2026-10-15T12:00:00+05:30\n").encode()
    import_csv(conn, settings.tenant_id, "purchase_order_lines", rekeyed)
    line = next(l for l in run_detection(conn, settings)["lines"] if l["po_number"] == "PO-502")
    assert (line["outcome"], line["reason"]) == ("NOT_EVALUABLE", "AMBIGUOUS_PO_LINE")


def test_rejected_rows_are_listed(client, conn, settings):
    import_csv(conn, settings.tenant_id, "purchase_order_lines", (DATA / "po_bad_rows.csv").read_bytes())
    rejected = client.get("/data-quality").json()["rejected_rows"]
    assert [r["row_number"] for r in rejected] == [2, 3, 4]
    assert all(r["errors"] and r["raw"] for r in rejected)


def test_unmapped_employee_location_is_listed(client, conn, settings):
    load_fixture_pack(conn, settings)
    newcomer = ("source_employee_id,display_name,employment_status,role,location_code,manager_employee_id,region_id\n"
                "HR-901,Test Person,ACTIVE,receiving_associate,ST-999,,REGION-MH\n").encode()
    import_csv(conn, settings.tenant_id, "employees", newcomer)
    assert {"dataset": "employees", "source_key": "HR-901", "field": "location_code", "value": "ST-999"} in \
           client.get("/data-quality").json()["unmapped_references"]
