from datetime import datetime, timedelta, timezone

from app.routing.eligibility import Candidate, RoutingPolicy, can_supervise, choose_assignee

IST = timezone(timedelta(hours=5, minutes=30))
AS_OF = datetime(2026, 10, 16, 15, 0, tzinfo=IST)
SEEN = datetime(2026, 10, 16, 14, 50, tzinfo=IST)


def emp(eid, role, status="ACTIVE", loc="STORE-014", avail="ON_SHIFT", seen=SEEN, region="REGION-MH"):
    return Candidate(eid, role, status, loc, region, avail, seen)


# The supplied roster, already mapped (ST-014 -> STORE-014, ST-015 -> STORE-015).
ROSTER = [
    emp("HR-101", "receiving_associate", avail="OFF_SHIFT"),
    emp("HR-102", "receiving_associate"),
    emp("HR-103", "receiving_associate", status="INACTIVE"),
    emp("HR-201", "store_manager"),
    emp("HR-401", "store_manager", loc="STORE-015"),
    emp("HR-501", "regional_operations", loc=None),
]


def reasons(decision):
    return {c.employee_id: c.reasons for c in decision.checks}


def test_store_014_goes_to_the_active_on_shift_associate():
    d = choose_assignee(ROSTER, "STORE-014", AS_OF)
    assert (d.assignee_id, d.matched_role, d.policy_version) == ("HR-102", "receiving_associate", "routing/v1")
    assert reasons(d) == {"HR-101": ("OFF_SHIFT",), "HR-102": (), "HR-103": ("NOT_ACTIVE",)}


def test_store_015_falls_back_to_the_store_manager():
    d = choose_assignee(ROSTER, "STORE-015", AS_OF)
    assert (d.assignee_id, d.matched_role) == ("HR-401", "store_manager")


def test_nobody_eligible_means_no_assignee():
    d = choose_assignee([emp("HR-401", "store_manager", loc="STORE-015", avail="OFF_SHIFT")], "STORE-015", AS_OF)
    assert (d.assignee_id, d.matched_role) == (None, None)
    assert reasons(d) == {"HR-401": ("OFF_SHIFT",)}


def test_stale_missing_or_future_samples_are_not_trusted():
    roster = [emp("HR-102", "receiving_associate", seen=AS_OF - timedelta(minutes=31)),
              emp("HR-104", "receiving_associate", avail=None, seen=None),
              emp("HR-105", "receiving_associate", seen=AS_OF + timedelta(minutes=1))]
    d = choose_assignee(roster, "STORE-014", AS_OF)
    assert d.assignee_id is None
    assert reasons(d) == {"HR-102": ("STALE_AVAILABILITY",), "HR-104": ("NO_AVAILABILITY_SAMPLE",),
                          "HR-105": ("NO_AVAILABILITY_SAMPLE",)}


def test_sample_exactly_at_the_age_limit_is_still_fresh():
    d = choose_assignee([emp("HR-102", "receiving_associate", seen=AS_OF - timedelta(minutes=30))], "STORE-014", AS_OF)
    assert d.assignee_id == "HR-102"


def test_freshness_limit_comes_from_the_policy():
    strict = RoutingPolicy(max_sample_age=timedelta(minutes=5))
    assert choose_assignee(ROSTER, "STORE-014", AS_OF, strict).assignee_id is None


def test_inactive_and_off_shift_reasons_accumulate():
    d = choose_assignee([emp("HR-109", "receiving_associate", status="INACTIVE", avail="OFF_SHIFT")], "STORE-014", AS_OF)
    assert reasons(d) == {"HR-109": ("NOT_ACTIVE", "OFF_SHIFT")}


def test_tie_breaks_on_lowest_employee_id():
    roster = [emp("HR-109", "receiving_associate"), emp("HR-105", "receiving_associate")]
    assert choose_assignee(roster, "STORE-014", AS_OF).assignee_id == "HR-105"


def test_regional_role_is_never_auto_assigned_store_work():
    d = choose_assignee([emp("HR-501", "regional_operations", loc=None)], "STORE-014", AS_OF)
    assert d.assignee_id is None and d.checks == ()


def test_employee_with_unmapped_store_is_not_a_candidate():
    assert choose_assignee([emp("HR-102", "receiving_associate", loc=None)], "STORE-014", AS_OF).assignee_id is None


def test_decision_serialises_its_reasoning():
    j = choose_assignee(ROSTER, "STORE-014", AS_OF).to_json()
    assert j["assignee_id"] == "HR-102" and j["as_of"] == AS_OF.isoformat()
    assert {"employee_id": "HR-103", "role": "receiving_associate", "eligible": False,
            "reasons": ["NOT_ACTIVE"]} in j["checks"]


def test_supervisors():
    assert can_supervise(emp("HR-201", "store_manager"), "STORE-014", "REGION-MH")
    assert not can_supervise(emp("HR-201", "store_manager"), "STORE-015", "REGION-MH")
    assert can_supervise(emp("HR-501", "regional_operations", loc=None), "STORE-015", "REGION-MH")
    assert not can_supervise(emp("HR-501", "regional_operations", loc=None), "STORE-015", "REGION-KA")
    assert not can_supervise(emp("HR-102", "receiving_associate"), "STORE-014", "REGION-MH")
    assert not can_supervise(emp("HR-201", "store_manager", status="INACTIVE"), "STORE-014", "REGION-MH")
