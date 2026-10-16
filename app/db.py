"""Postgres access: connections, the SQL migration runner, table discovery, JSON parameters."""
import json
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def connect(database_url: str) -> psycopg.Connection:
    # autocommit + explicit `with conn.transaction():` blocks, so every transaction
    # boundary is visible in the code that relies on it (D-51).
    return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)


def migrate(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations/*.sql in filename order, each in its own transaction. Returns what ran."""
    applied = []
    with connect(database_url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {row["filename"] for row in conn.execute("SELECT filename FROM schema_migrations")}
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


def to_jsonb(value: Any) -> Jsonb:
    """A JSONB parameter that also accepts Decimal, datetime and UUID values (written as strings)."""
    return Jsonb(value, dumps=lambda v: json.dumps(v, default=_json_default))


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "normalize"):  # Decimal: "8", never "8.000" or "8E+0"
        return format(value.normalize(), "f")
    return str(value)


def all_tables(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT tablename FROM pg_tables"
        " WHERE schemaname = 'public' AND tablename <> 'schema_migrations' ORDER BY tablename"
    )
    return [row["tablename"] for row in rows]
