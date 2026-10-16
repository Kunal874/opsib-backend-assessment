"""Regenerate docs/API_EXAMPLES.md from real calls through the app (FastAPI TestClient).

Run from the repo root:  python scripts/generate_api_examples.py
WARNING: it EMPTIES the database in DATABASE_URL (the dev database by default), runs the examples,
then reloads the six supplied fixtures and runs detection, leaving a clean demo state.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
from fastapi.testclient import TestClient  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.db import all_tables, connect  # noqa: E402
from app.main import create_app  # noqa: E402

BASE = "http://localhost:8000"
settings = load_settings()
out: list[str] = []


def reset_dev_db():
    with connect(settings.database_url) as c:
        c.execute(f"TRUNCATE {', '.join(all_tables(c))} RESTART IDENTITY CASCADE")


def clip(value, max_list=2, max_str=160):
    """Shorten long lists/strings so the examples stay readable; marks what was cut."""
    if isinstance(value, list):
        items = [clip(v, max_list, max_str) for v in value[:max_list]]
        return items + ([f"… {len(value) - max_list} more"] if len(value) > max_list else [])
    if isinstance(value, dict):
        return {k: clip(v, max_list, max_str) for k, v in value.items()}
    if isinstance(value, str) and len(value) > max_str:
        return value[:max_str] + "…"
    return value


def example(title, method, path, *, body=None, csv=None, actor=None, note=None, keep=None, max_list=2):
    headers = {}
    curl = [f'curl -X {method} "{BASE}{path}"']
    kwargs = {}
    if actor:
        headers["X-Actor-Id"] = actor
        curl.append(f'-H "X-Actor-Id: {actor}"')
    if csv:
        headers["Content-Type"] = "text/csv"
        kwargs["content"] = Path(csv).read_bytes()
        curl += ['-H "Content-Type: text/csv"', f"--data-binary @{csv}"]
    if body is not None:
        kwargs["json"] = body
        curl += ['-H "Content-Type: application/json"', f"-d '{json.dumps(body)}'"]
    r = client.request(method, path, headers=headers, **kwargs)
    data = r.json()
    shown = keep(data) if keep else data
    out.append(f"### {title}\n")
    if note:
        out.append(f"{note}\n")
    out.append("```bash\n" + " \\\n  ".join(curl) + "\n```\n")
    out.append(f"**{r.status_code}**\n")
    out.append("```json\n" + json.dumps(clip(shown, max_list), indent=2, default=str) + "\n```\n")
    return data


reset_dev_db()
with TestClient(create_app(settings)) as client:
    out.append("# API examples\n")
    out.append("Every request and response below was **captured from a real run** against the service with the "
               "supplied fixtures, by `scripts/generate_api_examples.py` (not hand-written). Long lists are shortened with "
               "`… N more`. Interactive docs: `http://localhost:8000/docs`; machine-readable: "
               "[`openapi.json`](openapi.json).\n")
    out.append("Errors always have the same shape: `{\"error\": {\"code\", \"message\", \"details\"}}`.\n")
    out.append("Write calls on tasks need `X-Actor-Id: <employee id>` — a stand-in for real authentication.\n")

    out.append("## 1. Imports\n")
    reports = {}
    for name in ("location_aliases", "product_aliases", "employees", "workforce_status", "goods_receipt_lines"):
        reports[name] = client.post(f"/imports/{name}?source_filename={name}.csv",
                                    content=Path(f"fixtures/{name}.csv").read_bytes(),
                                    headers={"Content-Type": "text/csv"}).json()
    po = example("Import a dataset (the PO file has one identical duplicate row)", "POST",
                 "/imports/purchase_order_lines?source_filename=purchase_order_lines.csv",
                 csv="fixtures/purchase_order_lines.csv",
                 note="The other five files are imported the same way (`/imports/<file name without .csv>`), "
                      "or all at once with `python -m app.cli load-fixtures fixtures`.")
    example("Read an import report", "GET", f"/imports/{po['import_id']}",
            keep=lambda d: {k: d[k] for k in ("import_id", "status", "counts", "rows")})
    example("Import the same file again — nothing changes", "POST",
            "/imports/purchase_order_lines", csv="fixtures/purchase_order_lines.csv",
            keep=lambda d: {k: d[k] for k in ("status", "counts")})
    example("Rows that fail validation are rejected with reasons; good rows still load", "POST",
            "/imports/purchase_order_lines", csv="tests/data/po_bad_rows.csv",
            keep=lambda d: {k: d[k] for k in ("status", "counts", "rows")}, max_list=3)
    example("A structurally wrong file fails the whole import, visibly", "POST",
            "/imports/purchase_order_lines", csv="tests/data/po_missing_column.csv",
            keep=lambda d: {"error": {k: d["error"][k] for k in ("code", "message")}})
    example("Same source ID with different content is a conflict — the original is kept", "POST",
            "/imports/purchase_order_lines", csv="tests/data/po_conflicting_duplicate.csv",
            keep=lambda d: {k: d[k] for k in ("status", "counts", "rows")})

    # Keep the business examples clean: start again from the supplied fixtures only.
    reset_dev_db()
    for name in ("location_aliases", "product_aliases", "employees", "workforce_status",
                 "purchase_order_lines", "goods_receipt_lines"):
        client.post(f"/imports/{name}", content=Path(f"fixtures/{name}.csv").read_bytes(),
                    headers={"Content-Type": "text/csv"})

    out.append("## 2. Detection\n")
    out.append("_The examples below start again from the six supplied files only._\n")
    example("Run detection (as_of defaults to the frozen 2026-10-16T15:00:00+05:30)", "POST", "/detection/runs",
            keep=lambda d: {**{k: d[k] for k in ("as_of", "rule_version", "counts", "exceptions", "tasks_created")},
                            "lines": [{k: l[k] for k in ("po_number", "outcome", "reason", "expected_qty",
                                                         "received_qty", "variance_qty")} for l in d["lines"]]},
            max_list=6)
    example("Run it again — idempotent", "POST", "/detection/runs",
            keep=lambda d: {k: d[k] for k in ("exceptions", "tasks_created")})
    example("Override the evaluation time (`+` must be sent as `%2B`)", "POST",
            "/detection/runs?as_of=2026-10-18T09:00:00%2B05:30",
            keep=lambda d: {"lines": [{k: l[k] for k in ("po_number", "outcome", "variance_qty")}
                                      for l in d["lines"] if l["po_number"] == "PO-503"]},
            note="PO-503's deadline (17 Oct 18:00) has passed by 18 Oct, so its 30 EA gap becomes a shortfall.")
    # Undo that extra finding so the rest of the examples match the brief exactly.
    reset_dev_db()
    for name in ("location_aliases", "product_aliases", "employees", "workforce_status",
                 "purchase_order_lines", "goods_receipt_lines"):
        client.post(f"/imports/{name}", content=Path(f"fixtures/{name}.csv").read_bytes(),
                    headers={"Content-Type": "text/csv"})
    client.post("/detection/runs")
    example("Data quality (clean fixtures → nothing to report)", "GET", "/data-quality")

    out.append("## 3. Exceptions\n")
    listed = example("List exceptions", "GET", "/exceptions", max_list=3)
    by_po = {e["po_number"]: e["exception_id"] for e in listed}
    example("One exception with evidence, source records (down to the CSV line), tasks and history", "GET",
            f"/exceptions/{by_po['PO-505']}", max_list=4,
            keep=lambda d: {**{k: d[k] for k in ("exception_id", "business_key", "status", "rule_version",
                                                   "canonical_product_id", "canonical_location_id", "expected_qty",
                                                   "received_qty", "variance_qty", "uom", "evidence",
                                                   "explanation", "unknowns")},
                            "source_records": {t: [{k: r[k] for k in ("source_record_id", "import_id",
                                                                      "source_row_number", "raw_row")}
                                                   for r in rows] for t, rows in d["source_records"].items()},
                            "tasks": d["tasks"], "history": [h["event_type"] for h in d["history"]]})
    example("Optional AI summary (disabled by default)", "GET", f"/exceptions/{by_po['PO-505']}/ai-summary")

    out.append("## 4. Tasks\n")
    tasks = example("Tasks for an assignee", "GET", "/tasks?assignee_id=HR-102",
                    keep=lambda d: [{k: t[k] for k in ("task_id", "po_number", "status", "assignee_employee_id",
                                                       "canonical_location_id", "due_at", "variance_qty")} for t in d])
    t501 = next(t["task_id"] for t in tasks if t["po_number"] == "PO-501")
    t506 = next(t["task_id"] for t in tasks if t["po_number"] == "PO-506")
    status = lambda d: {"task": {"status": d["task"]["status"], "assignee_employee_id": d["task"]["assignee_employee_id"]},
                        "replayed": d["replayed"]} if "task" in d else d
    example("Skipping a state is refused", "POST", f"/tasks/{t501}/complete", actor="HR-102",
            body={"resolution_code": "SHORTFALL_CONFIRMED", "evidence_ref": "RECOUNT-0042"})
    example("Only the assignee can work the task", "POST", f"/tasks/{t501}/acknowledge", actor="HR-101")
    example("Acknowledge", "POST", f"/tasks/{t501}/acknowledge", actor="HR-102", keep=status)
    example("Start", "POST", f"/tasks/{t501}/start", actor="HR-102", keep=status)
    done = {"resolution_code": "SHORTFALL_CONFIRMED", "evidence_ref": "RECOUNT-0042"}
    example("Complete (goes straight to PENDING_VERIFICATION)", "POST", f"/tasks/{t501}/complete",
            actor="HR-102", body=done, keep=status)
    example("The client timed out and retries the same request → same answer, nothing written twice", "POST",
            f"/tasks/{t501}/complete", actor="HR-102", body=done, keep=status)
    example("A retry with different data is refused", "POST", f"/tasks/{t501}/complete", actor="HR-102",
            body={**done, "evidence_ref": "OTHER-1"})
    example("The assignee cannot verify their own work", "POST", f"/tasks/{t501}/verify", actor="HR-102")
    example("The store manager verifies → the exception is RESOLVED", "POST", f"/tasks/{t501}/verify",
            actor="HR-201", keep=lambda d: {"task": {"status": d["task"]["status"],
                                                     "exception": d["task"]["exception"]}, "replayed": d["replayed"]})
    example("Full audit history of the task", "GET", f"/tasks/{t501}",
            keep=lambda d: {"history": [{k: h[k] for k in ("event_type", "actor", "from_status", "to_status")}
                                        for h in d["history"]]}, max_list=10)
    example("Reassigning to an ineligible person is refused", "POST", f"/tasks/{t506}/reassign", actor="HR-201",
            body={"new_assignee_id": "HR-101", "reason": "cover for the afternoon"})
    example("Reassign with a reason", "POST", f"/tasks/{t506}/reassign", actor="HR-201",
            body={"new_assignee_id": "HR-201", "reason": "associate busy"}, keep=status)

# Leave the dev DB in the clean state the README describes.
reset_dev_db()
with TestClient(create_app(settings)) as client:
    for name in ("location_aliases", "product_aliases", "employees", "workforce_status",
                 "purchase_order_lines", "goods_receipt_lines"):
        client.post(f"/imports/{name}", content=Path(f"fixtures/{name}.csv").read_bytes(),
                    headers={"Content-Type": "text/csv"})
    client.post("/detection/runs")

Path("docs/API_EXAMPLES.md").write_text("\n".join(out), encoding="utf-8", newline="\n")
print(f"wrote docs/API_EXAMPLES.md ({sum(s.count(chr(10)) for s in out)} lines)")
