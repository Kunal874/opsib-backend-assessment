"""FastAPI dependencies shared by every router."""
from collections.abc import Iterator

import psycopg
from fastapi import Request

from app.config import Settings
from app.db import connect


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    # ponytail: one connection per request; add psycopg_pool or PgBouncer if throughput matters (D-48)
    with connect(request.app.state.settings.database_url) as conn:
        yield conn
