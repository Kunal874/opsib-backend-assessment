"""Who may be assigned a task — policy routing/v1, as pure functions (D-35..D-37).

Eligible = role matches the current step of the chain, AND mapped home store == task store, AND HRMS says
ACTIVE, AND the latest availability sample at or before as_of says ON_SHIFT and is fresh enough.
HRMS employment status beats the shift export (HR-103); the observed status beats the scheduled shift (HR-101).
Nobody eligible -> no assignee. An ineligible person is never assigned to fill the gap.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

POLICY_VERSION = "routing/v1"


@dataclass(frozen=True)
class RoutingPolicy:
    role_chain: tuple[str, ...] = ("receiving_associate", "store_manager")
    max_sample_age: timedelta = timedelta(minutes=30)
    version: str = POLICY_VERSION


@dataclass(frozen=True)
class Candidate:
    employee_id: str
    role: str
    employment_status: str
    location_id: str | None          # canonical store via the HRMS alias; None if region-scoped or unmapped
    region_id: str | None
    availability_status: str | None  # latest sample at or before as_of
    observed_at: datetime | None


@dataclass(frozen=True)
class CandidateCheck:
    employee_id: str
    role: str
    eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RoutingDecision:
    assignee_id: str | None
    matched_role: str | None
    checks: tuple[CandidateCheck, ...]  # everyone considered, in order, with why they were (not) chosen
    policy_version: str
    as_of: datetime

    def to_json(self) -> dict:
        return {
            "assignee_id": self.assignee_id,
            "matched_role": self.matched_role,
            "policy_version": self.policy_version,
            "as_of": self.as_of.isoformat(),
            "checks": [{"employee_id": c.employee_id, "role": c.role, "eligible": c.eligible,
                        "reasons": list(c.reasons)} for c in self.checks],
        }


def check_eligibility(c: Candidate, as_of: datetime, policy: RoutingPolicy) -> CandidateCheck:
    reasons = []
    if c.employment_status != "ACTIVE":
        reasons.append("NOT_ACTIVE")
    if c.observed_at is None or c.observed_at > as_of:
        reasons.append("NO_AVAILABILITY_SAMPLE")
    elif as_of - c.observed_at > policy.max_sample_age:
        reasons.append("STALE_AVAILABILITY")  # an old sample is not trusted either way
    elif c.availability_status != "ON_SHIFT":
        reasons.append("OFF_SHIFT")
    return CandidateCheck(c.employee_id, c.role, not reasons, tuple(reasons))


def choose_assignee(candidates: Sequence[Candidate], location_id: str, as_of: datetime,
                    policy: RoutingPolicy = RoutingPolicy()) -> RoutingDecision:
    checks: list[CandidateCheck] = []
    for role in policy.role_chain:
        pool = sorted((c for c in candidates if c.role == role and c.location_id == location_id),
                      key=lambda c: c.employee_id)  # deterministic tie-break: lowest employee ID
        role_checks = [check_eligibility(c, as_of, policy) for c in pool]
        checks.extend(role_checks)
        chosen = next((check for check in role_checks if check.eligible), None)
        if chosen:
            return RoutingDecision(chosen.employee_id, role, tuple(checks), policy.version, as_of)
    return RoutingDecision(None, None, tuple(checks), policy.version, as_of)


def can_supervise(actor: Candidate, location_id: str, region_id: str | None) -> bool:
    """Supervisors verify, reject and reassign: the store's manager, or regional operations for its region."""
    if actor.employment_status != "ACTIVE":
        return False
    if actor.role == "store_manager":
        return actor.location_id == location_id
    if actor.role == "regional_operations":
        return region_id is not None and actor.region_id == region_id
    return False
