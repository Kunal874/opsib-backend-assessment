"""Detection endpoint. as_of defaults to the frozen evaluation time; pass it URL-encoded (+ as %2B)."""
import psycopg
from fastapi import APIRouter, Depends
from pydantic import AwareDatetime

from app.api.deps import get_conn, get_settings
from app.config import Settings
from app.detection.service import run_detection

router = APIRouter(tags=["detection"])


@router.post("/detection/runs", status_code=201, summary="Evaluate every PO line and persist findings")
def create_detection_run(as_of: AwareDatetime | None = None, conn: psycopg.Connection = Depends(get_conn),
                         settings: Settings = Depends(get_settings)) -> dict:
    return run_detection(conn, settings, as_of)
