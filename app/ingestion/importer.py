"""Idempotent CSV import: validate every row, keep the raw input, store each source fact once.

Transaction boundaries (D-09, D-10):
  1. The import_runs row is committed on its own first, so even a crash leaves a visible record.
  2. All row work for the file (raw rows, facts, conflicts, final counts) commits together or not at all.
  3. If step 2 fails, a FAILED status is written in its own small transaction.

Duplicates are decided by Postgres, never by a Python check-then-insert (D-14):
  INSERT ... ON CONFLICT DO NOTHING; if nothing was inserted, compare content hashes:
  same hash -> DUPLICATE (no effect), different hash -> CONFLICT (original kept, clash recorded) (D-15, D-16).
"""
import csv
import hashlib
import io
import uuid
from collections.abc import Iterator

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from app.errors import AppError
from app.ingestion.datasets import DATASETS, DatasetSpec
from app.ingestion.rows import content_hash
from app.log import log

# Any order works (mapping happens at evaluation time, D-18); this one just reads naturally.
LOAD_ORDER = ("location_aliases", "product_aliases", "employees", "workforce_status",
              "purchase_order_lines", "goods_receipt_lines")
OUTCOMES = ("accepted", "duplicate", "conflict", "rejected")


class BadCsv(Exception):
    """The file itself is unusable (encoding, header) — as opposed to a bad row inside it."""


def import_csv(conn: psycopg.Connection, tenant_id: str, dataset: str, content: bytes,
               source_filename: str | None = None) -> dict:
    spec = DATASETS.get(dataset)
    if spec is None:
        raise AppError(404, "UNKNOWN_DATASET", f"Unknown dataset '{dataset}'", {"known": sorted(DATASETS)})

    import_id = uuid.uuid4()
    with conn.transaction():
        conn.execute(
            "INSERT INTO import_runs (import_id, tenant_id, dataset, source_filename, file_sha256, status)"
            " VALUES (%s, %s, %s, %s, %s, 'PROCESSING')",
            (import_id, tenant_id, dataset, source_filename, hashlib.sha256(content).hexdigest()),
        )

    try:
        reader = _open_csv(content, spec)
        with conn.transaction():
            counts = _import_rows(conn, tenant_id, spec, import_id, reader)
            conn.execute(
                "UPDATE import_runs SET status = 'COMPLETED', total_rows = %(total)s, accepted_rows = %(accepted)s,"
                " duplicate_rows = %(duplicate)s, conflict_rows = %(conflict)s, rejected_rows = %(rejected)s,"
                " finished_at = now() WHERE import_id = %(import_id)s",
                {**counts, "import_id": import_id},
            )
    except Exception as exc:
        # The row transaction has rolled back as a unit; record why, then surface it.
        expected = isinstance(exc, BadCsv)
        message = str(exc) if expected else f"{type(exc).__name__}: import aborted, no rows applied"
        with conn.transaction():
            conn.execute(
                "UPDATE import_runs SET status = 'FAILED', error = %s, finished_at = now() WHERE import_id = %s",
                (message, import_id),
            )
        if not expected:
            log.exception("import_crashed", extra={"fields": {"import_id": str(import_id), "dataset": dataset}})
            raise

    report = get_import_report(conn, tenant_id, import_id)
    log.info("import_finished", extra={"fields": {
        "import_id": report["import_id"], "dataset": dataset, "status": report["status"], **report["counts"]}})
    return report


def get_import_report(conn: psycopg.Connection, tenant_id: str, import_id: uuid.UUID | str) -> dict:
    run = conn.execute(
        "SELECT * FROM import_runs WHERE tenant_id = %s AND import_id = %s", (tenant_id, import_id)
    ).fetchone()
    if run is None:
        raise AppError(404, "NOT_FOUND", f"Import {import_id} not found")
    problem_rows = conn.execute(
        "SELECT row_number, source_key, outcome, errors FROM import_rows"
        " WHERE import_id = %s AND outcome <> 'ACCEPTED' ORDER BY row_number",
        (import_id,),
    ).fetchall()
    return {
        "import_id": str(run["import_id"]),
        "dataset": run["dataset"],
        "status": run["status"],
        "source_filename": run["source_filename"],
        "counts": {"total": run["total_rows"], **{o: run[f"{o}_rows"] for o in OUTCOMES}},
        "error": run["error"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "rows": problem_rows,  # every non-ACCEPTED row, with its reasons
    }


def _open_csv(content: bytes, spec: DatasetSpec) -> csv.DictReader:
    try:
        text = content.decode("utf-8-sig")  # tolerates a byte-order mark
    except UnicodeDecodeError as exc:
        raise BadCsv("File is not valid UTF-8 text") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))  # newline="": csv handles CRLF itself
    missing = sorted(set(spec.required_columns) - set(reader.fieldnames or []))
    if missing:
        raise BadCsv(f"Missing required columns: {', '.join(missing)}")
    return reader


def _import_rows(conn: psycopg.Connection, tenant_id: str, spec: DatasetSpec, import_id: uuid.UUID,
                 reader: csv.DictReader) -> dict:
    counts = dict.fromkeys(("total", *OUTCOMES), 0)
    for raw in _raw_rows(reader):
        row_number = reader.line_num  # the file's own line number (header = 1), even for multi-line fields
        source_key, errors = None, []
        try:
            row = spec.row_model.model_validate(raw)
        except ValidationError as exc:
            outcome = "REJECTED"
            errors = [f"{'.'.join(map(str, e['loc'])) or 'row'}: {e['msg']}" for e in exc.errors()]
        else:
            source_key = spec.source_key(row)
            outcome = _store(conn, tenant_id, spec, import_id, row_number, row, source_key)
        conn.execute(
            "INSERT INTO import_rows (import_id, row_number, source_key, outcome, errors, raw)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (import_id, row_number, source_key, outcome, Jsonb(errors), Jsonb(raw)),
        )
        counts["total"] += 1
        counts[outcome.lower()] += 1
    return counts


def _raw_rows(reader: csv.DictReader) -> Iterator[dict]:
    for raw in reader:
        # csv puts surplus cells under the key None; give them a JSON-safe name instead of losing them.
        yield {("_extra_cells" if key is None else key): value for key, value in raw.items()}


def _store(conn: psycopg.Connection, tenant_id: str, spec: DatasetSpec, import_id: uuid.UUID,
           row_number: int, row: BaseModel, source_key: str) -> str:
    row_hash = content_hash(row)
    # Identifiers below come only from the DATASETS registry, never from request input.
    columns = ["tenant_id", *spec.required_columns, "content_hash", "import_id"]
    inserted = conn.execute(
        f"INSERT INTO {spec.name} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})"
        f" ON CONFLICT (tenant_id, {', '.join(spec.key_fields)}) DO NOTHING RETURNING 1",
        [tenant_id, *(getattr(row, c) for c in spec.required_columns), row_hash, import_id],
    ).fetchone()
    if inserted:
        return "ACCEPTED"

    where = " AND ".join(f"{field} = %s" for field in ("tenant_id", *spec.key_fields))
    existing = conn.execute(
        f"SELECT content_hash FROM {spec.name} WHERE {where}",
        [tenant_id, *(getattr(row, field) for field in spec.key_fields)],
    ).fetchone()
    if existing["content_hash"] == row_hash:
        return "DUPLICATE"

    conn.execute(
        "INSERT INTO source_conflicts (tenant_id, dataset, source_key, existing_hash, incoming_hash,"
        " incoming_row, import_id, row_number) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (tenant_id, dataset, source_key, incoming_hash) DO NOTHING",
        (tenant_id, spec.name, source_key, existing["content_hash"], row_hash,
         Jsonb(row.model_dump(mode="json")), import_id, row_number),
    )
    log.warning("source_conflict", extra={"fields": {"dataset": spec.name, "source_key": source_key,
                                                     "import_id": str(import_id), "row_number": row_number}})
    return "CONFLICT"
