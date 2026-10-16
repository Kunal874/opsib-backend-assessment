"""Import endpoints. The CSV is the raw request body (Content-Type: text/csv) — D-06."""
import uuid

import psycopg
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_conn, get_settings
from app.config import Settings
from app.errors import AppError
from app.ingestion.importer import get_import_report, import_csv

router = APIRouter(tags=["imports"])

CSV_BODY = {"requestBody": {"required": True, "content": {"text/csv": {"schema": {"type": "string"}}}}}


@router.post("/imports/{dataset}", status_code=201, openapi_extra=CSV_BODY,
             summary="Validate and load one dataset CSV")
async def create_import(dataset: str, request: Request, source_filename: str | None = None,
                        conn: psycopg.Connection = Depends(get_conn),
                        settings: Settings = Depends(get_settings)) -> dict:
    content = await request.body()
    # The importer is blocking DB work: run it off the event loop.
    report = await run_in_threadpool(import_csv, conn, settings.tenant_id, dataset, content, source_filename)
    if report["status"] == "FAILED":
        raise AppError(422, "BAD_CSV", report["error"], report)
    return report


@router.get("/imports/{import_id}", summary="Counts, row-level errors and status of one import")
def read_import(import_id: uuid.UUID, conn: psycopg.Connection = Depends(get_conn),
                settings: Settings = Depends(get_settings)) -> dict:
    return get_import_report(conn, settings.tenant_id, import_id)
