"""Exception endpoints: list, inspect with source records, optional AI summary."""
import uuid
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, Request

from app.ai.summary import build_evidence_package, summarize_exception
from app.api.deps import get_conn, get_settings
from app.config import Settings
from app.detection.read import get_exception, list_exceptions

router = APIRouter(tags=["exceptions"])
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
Config = Annotated[Settings, Depends(get_settings)]


@router.get("/exceptions", summary="List findings (filter by status and/or store)")
def read_exceptions(conn: Conn, settings: Config, status: Literal["OPEN", "CLOSED", "RESOLVED"] | None = None,
                    location_id: str | None = None) -> list[dict]:
    return list_exceptions(conn, settings.tenant_id, status, location_id)


@router.get("/exceptions/{exception_id}",
            summary="One finding with its evidence, source records (down to the CSV line), tasks and history")
def read_exception(exception_id: uuid.UUID, conn: Conn, settings: Config) -> dict:
    return get_exception(conn, settings.tenant_id, exception_id)


@router.get("/exceptions/{exception_id}/ai-summary",
            summary="Optional AI explanation, validated against the evidence; UNAVAILABLE when disabled")
def read_ai_summary(exception_id: uuid.UUID, request: Request, conn: Conn, settings: Config) -> dict:
    package = build_evidence_package(get_exception(conn, settings.tenant_id, exception_id))
    return {"exception_id": str(exception_id),
            **summarize_exception(request.app.state.summary_provider, package)}
