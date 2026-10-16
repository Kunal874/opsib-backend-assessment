import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.db import all_tables, connect, migrate
from app.main import create_app

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql://opsib:opsib@localhost:55432/opsib_test")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture(scope="session")
def settings():
    return load_settings({"DATABASE_URL": TEST_DB})


@pytest.fixture(scope="session")
def _schema(settings):
    # Once per session: rebuild the schema from the migrations, so tests always run on the real DDL.
    with psycopg.connect(settings.database_url, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    migrate(settings.database_url)


@pytest.fixture
def conn(settings, _schema):
    # Per test: empty every table (cheap) so tests can't leak state into each other.
    with connect(settings.database_url) as c:
        tables = all_tables(c)
        if tables:
            c.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
        yield c


@pytest.fixture
def client(settings, conn):
    with TestClient(create_app(settings)) as c:
        yield c
