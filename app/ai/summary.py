"""The AI boundary (D-49): an optional provider may *explain* a finding — never decide anything.

  deterministic detector --> finding + evidence IDs --> EvidencePackage (case facts only, no employee data)
                                                    --> provider.summarize() --> InvestigationSummary (validated)

The provider sees only the package. Its output must pass a strict schema (unknown fields are rejected, so it
cannot slip in "verified": true or a new quantity), and every record it cites must be one we supplied.
Timeouts, errors and invalid output degrade to UNAVAILABLE / INVALID — the rest of the service is unaffected.
Authoritative numbers always come from the finding, never from the model.

No live model is wired in: DisabledProvider is the default and tests use FakeProvider. A Claude-backed
provider would implement the same one-method protocol (send the package JSON, return the parsed reply).
"""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class EvidencePackage(BaseModel):
    exception_id: str
    exception_type: str
    facts: dict[str, str]
    evidence_ids: list[str]
    unknowns: list[str]


class InvestigationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no smuggled decisions

    summary: str = Field(min_length=1, max_length=2000)
    missing_context: list[str] = Field(default_factory=list, max_length=10)
    suggested_steps: list[str] = Field(default_factory=list, max_length=5)
    cited_evidence_ids: list[str] = Field(default_factory=list)


class ProviderUnavailable(Exception):
    pass


class SummaryProvider(Protocol):
    def summarize(self, package: EvidencePackage) -> Any: ...


class DisabledProvider:
    def summarize(self, package: EvidencePackage) -> Any:
        raise ProviderUnavailable("AI summaries are disabled")


class FakeProvider:
    """For tests: returns the given output, or raises the given error."""

    def __init__(self, output: Any = None, error: Exception | None = None):
        self.output, self.error = output, error

    def summarize(self, package: EvidencePackage) -> Any:
        if self.error:
            raise self.error
        return self.output


def build_evidence_package(exception: dict) -> EvidencePackage:
    fact_fields = ("po_number", "po_line_number", "canonical_product_id", "canonical_location_id", "expected_qty",
                   "received_qty", "variance_qty", "uom", "evaluated_as_of", "rule_version")
    evidence = exception["evidence"]
    return EvidencePackage(
        exception_id=str(exception["exception_id"]),
        exception_type=exception["exception_type"],
        facts={field: str(exception[field]) for field in fact_fields},
        evidence_ids=[*evidence["po_source_ids"], *evidence["receipt_source_ids"]],
        unknowns=list(exception["unknowns"]),
    )


def summarize_exception(provider: SummaryProvider, package: EvidencePackage, timeout_s: float = 10.0) -> dict:
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        raw = pool.submit(provider.summarize, package).result(timeout=timeout_s)
    except FutureTimeout:
        return _result("UNAVAILABLE", error=f"provider timed out after {timeout_s}s")
    except Exception as exc:
        return _result("UNAVAILABLE", error=f"provider failed: {type(exc).__name__}: {exc}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # never block the caller on a slow model

    try:
        summary = InvestigationSummary.model_validate(raw)
    except ValidationError as exc:
        return _result("INVALID", error=f"output failed schema validation ({exc.error_count()} errors)")
    invented = sorted(set(summary.cited_evidence_ids) - set(package.evidence_ids))
    if invented:
        return _result("INVALID", error=f"cited records that are not in the evidence: {invented}")
    return _result("OK", summary=summary.model_dump())


def _result(status: str, summary: dict | None = None, error: str | None = None) -> dict:
    return {"status": status, "summary": summary, "error": error}
