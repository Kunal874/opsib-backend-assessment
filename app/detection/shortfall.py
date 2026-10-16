"""The receipt-shortfall rule: a pure function — resolved data in, decision out (D-23..D-28).

No database, no clock, no I/O: "now" is the `as_of` argument, so the same inputs always give the same
answer. The rule only ever sees canonical, base-unit data from the resolver, never CSV columns — which
is why another retailer's export can be added without touching it.

Check order (each step can end the evaluation):
  1. blocked (open source conflict / ambiguous PO identity)   -> NOT_EVALUABLE
  2. PO status: CANCELLED -> CANCELLED; anything but OPEN       -> NOT_EVALUABLE (allow-list, D-52)
  3. PO mapping issue                                           -> NOT_EVALUABLE
  4. deadline not strictly passed                               -> NOT_DUE (D-25)
  5. per distinct receipt: unposted / received after as_of -> ignored (listed);
     unmapped or mapped to another product/location/unit   -> NOT_EVALUABLE
  6. variance = expected - received; > tolerance -> SHORTFALL, else NO_SHORTFALL
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.mapping.resolver import ResolvedPoLine, ResolvedReceipt

RULE_VERSION = "receipt_shortfall/v1"


class Outcome(StrEnum):
    SHORTFALL = "SHORTFALL"
    NO_SHORTFALL = "NO_SHORTFALL"
    NOT_DUE = "NOT_DUE"
    CANCELLED = "CANCELLED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


@dataclass(frozen=True)
class Evaluation:
    outcome: Outcome
    reason: str | None = None
    expected_qty: Decimal | None = None
    received_qty: Decimal | None = None
    variance_qty: Decimal | None = None
    counted_receipt_ids: tuple[str, ...] = ()
    ignored_receipts: tuple[tuple[str, str], ...] = ()  # (source_record_id, why it was not counted)


def evaluate_shortfall(po: ResolvedPoLine, receipts: Sequence[ResolvedReceipt], as_of: datetime,
                       tolerance: Decimal = Decimal(0), *, blocked_by: str | None = None) -> Evaluation:
    if blocked_by:
        return Evaluation(Outcome.NOT_EVALUABLE, reason=blocked_by)
    if po.status == "CANCELLED":
        return Evaluation(Outcome.CANCELLED)
    if po.status != "OPEN":
        return Evaluation(Outcome.NOT_EVALUABLE, reason="UNKNOWN_PO_STATUS")
    if po.issue:
        return Evaluation(Outcome.NOT_EVALUABLE, reason=po.issue)
    if as_of <= po.expected_receipt_by:  # actionable only once the deadline has *passed*
        return Evaluation(Outcome.NOT_DUE, expected_qty=po.expected_qty)

    # One receipt fact per source record ID, in a stable order: the grain is the source line, not the CSV row.
    distinct = sorted({r.source_record_id: r for r in receipts}.values(), key=lambda r: r.source_record_id)
    counted: list[ResolvedReceipt] = []
    ignored: list[tuple[str, str]] = []
    for receipt in distinct:
        if receipt.posting_status != "POSTED":
            ignored.append((receipt.source_record_id, "NOT_POSTED"))
        elif receipt.received_at > as_of:
            ignored.append((receipt.source_record_id, "RECEIVED_AFTER_AS_OF"))
        elif receipt.issue:
            return Evaluation(Outcome.NOT_EVALUABLE, reason=f"RECEIPT_{receipt.issue}")
        elif (receipt.canonical_product_id, receipt.canonical_location_id, receipt.base_uom) != \
                (po.canonical_product_id, po.canonical_location_id, po.base_uom):
            return Evaluation(Outcome.NOT_EVALUABLE, reason="RECEIPT_MISMATCH")
        else:
            counted.append(receipt)

    received = sum((r.accepted_qty for r in counted), Decimal(0))
    variance = po.expected_qty - received
    return Evaluation(
        Outcome.SHORTFALL if variance > tolerance else Outcome.NO_SHORTFALL,
        expected_qty=po.expected_qty,
        received_qty=received,
        variance_qty=variance,
        counted_receipt_ids=tuple(r.source_record_id for r in counted),
        ignored_receipts=tuple(ignored),
    )
