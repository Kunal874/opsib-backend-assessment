"""Service settings, read from environment variables (documented in .env.example)."""
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class Settings:
    database_url: str
    tenant_id: str
    evaluation_time: datetime  # frozen business "now" — the only clock scenario logic may read (D-24)
    shortfall_tolerance: Decimal
    workforce_max_sample_age: timedelta
    task_sla: timedelta
    log_level: str


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    evaluation_time = datetime.fromisoformat(env.get("EVALUATION_TIME", "2026-10-16T15:00:00+05:30"))
    if evaluation_time.tzinfo is None:
        raise ValueError("EVALUATION_TIME must include a UTC offset, e.g. 2026-10-16T15:00:00+05:30")
    tolerance = Decimal(env.get("SHORTFALL_TOLERANCE", "0"))
    if tolerance < 0:
        raise ValueError("SHORTFALL_TOLERANCE must be >= 0")
    return Settings(
        database_url=env.get("DATABASE_URL", "postgresql://opsib:opsib@localhost:55432/opsib"),
        tenant_id=env.get("TENANT_ID", "demo-retailer"),
        evaluation_time=evaluation_time,
        shortfall_tolerance=tolerance,
        workforce_max_sample_age=timedelta(minutes=int(env.get("WORKFORCE_MAX_SAMPLE_AGE_MINUTES", "30"))),
        task_sla=timedelta(hours=int(env.get("TASK_SLA_HOURS", "24"))),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )
