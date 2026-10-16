"""Loading routing candidates: the HRMS roster joined to each person's latest availability sample."""
from datetime import datetime

import psycopg

from app.mapping.resolver import AliasIndex, resolve_location
from app.routing.eligibility import Candidate

CANDIDATES_SQL = """
SELECT e.source_employee_id, e.source_system, e.role, e.employment_status, e.location_code, e.region_id,
       w.availability_status, w.observed_at
  FROM employees e
  LEFT JOIN LATERAL (
        SELECT availability_status, observed_at
          FROM workforce_status w
         WHERE w.tenant_id = e.tenant_id AND w.source_employee_id = e.source_employee_id
           AND w.observed_at <= %(as_of)s            -- never peek at samples from the future
         ORDER BY w.observed_at DESC
         LIMIT 1) w ON true
 WHERE e.tenant_id = %(tenant_id)s
 ORDER BY e.source_employee_id
"""


def load_candidates(conn: psycopg.Connection, tenant_id: str, as_of: datetime, index: AliasIndex) -> list[Candidate]:
    candidates = []
    for r in conn.execute(CANDIDATES_SQL, {"tenant_id": tenant_id, "as_of": as_of}):
        # Employee locations must be mapped before they can be used for routing (brief, Task A item 2).
        store = resolve_location(index, r["source_system"], r["location_code"])
        candidates.append(Candidate(
            employee_id=r["source_employee_id"],
            role=r["role"],
            employment_status=r["employment_status"],
            location_id=store.canonical_location_id if store else None,
            region_id=r["region_id"] or (store.region_id if store else None),
            availability_status=r["availability_status"],
            observed_at=r["observed_at"],
        ))
    return candidates
