from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config import load_settings
from app.db import migrate


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_settings_defaults_use_frozen_time():
    s = load_settings({})
    assert s.evaluation_time == datetime(2026, 10, 16, 15, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert s.shortfall_tolerance == Decimal("0")
    assert s.workforce_max_sample_age == timedelta(minutes=30)
    assert s.task_sla == timedelta(hours=24)
    assert s.tenant_id == "demo-retailer"


def test_settings_reject_naive_evaluation_time():
    with pytest.raises(ValueError):
        load_settings({"EVALUATION_TIME": "2026-10-16T15:00:00"})


def test_migrate_is_idempotent(settings, conn):
    assert migrate(settings.database_url) == []          # already applied by the session fixture


def test_unknown_route_uses_standard_error_shape(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
