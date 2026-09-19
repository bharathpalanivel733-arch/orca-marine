"""Deterministic verification rules (PLAN.md Phase 6.1, 6.3).

**The rules decide.** Each is arithmetic against a stated threshold, so the same evidence
always produces the same verdict and a jury can be shown the number that triggered a
refusal. The critique step (``critique.py``) runs afterwards and may only *add* doubt; it
cannot overturn a rule.

That ordering is the whole design. A language model asked to adjudicate safety evidence
would be unreproducible, unauditable, and would occasionally be argued out of a correct
refusal. Running it second, in an advisory role, keeps its value — it can notice what the
rules did not anticipate — without giving it authority it should not have.

These checks replace the "7-point integrity check" framing (PLAN.md 6.5): each is named,
each states its threshold, and each reports the observed value it compared.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from orca_schemas import BoundingBox, EvidenceProvenance

from orca_trust.verdict import CheckName, CheckResult, CheckSeverity

# A safety verdict needs at least this much corroboration before it is offered at all.
MIN_EVIDENCE_ITEMS = 2

# Evidence older than this is never fresh enough for a safety verdict, regardless of the
# source's own cadence. A source publishing every 56 h does not make a 56-hour-old wave
# forecast usable for "is it safe tomorrow morning".
ABSOLUTE_FRESHNESS_LIMIT = timedelta(hours=12)

# Sources ORCA is willing to treat as authoritative. An unknown source id is not a
# warning, it is a blocking failure: evidence whose origin cannot be established has no
# place in a safety decision.
KNOWN_SOURCES: frozenset[str] = frozenset(
    {
        "incois_erddap",
        "incois_pfz",
        "incois_osf",
        "imd",
        "cmems",
        "mosdac",
        "niot_omni",
        "open_meteo_marine",
    }
)


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence the verifier weighs.

    Deliberately flat rather than reusing ``ObservationRecord``: the verifier must also be
    able to judge retrieved passages and kernel inputs, which are not observations.
    """

    provenance: EvidenceProvenance
    variable: str | None = None
    value: float | None = None
    lat: float | None = None
    lon: float | None = None
    cadence_deadline: timedelta | None = None

    def age(self, now: datetime) -> timedelta:
        return self.provenance.age(now)


def check_source_validity(items: Sequence[EvidenceItem]) -> CheckResult:
    """Every source must be one ORCA recognises."""
    unknown = sorted(
        {i.provenance.source for i in items if i.provenance.source not in KNOWN_SOURCES}
    )
    if not unknown:
        return CheckResult(
            name=CheckName.SOURCE_VALIDITY,
            passed=True,
            severity=CheckSeverity.INFO,
            detail=f"all {len(items)} evidence item(s) come from recognised sources",
            observed={"sources": sorted({i.provenance.source for i in items})},
        )
    return CheckResult(
        name=CheckName.SOURCE_VALIDITY,
        passed=False,
        severity=CheckSeverity.BLOCKING,
        detail=f"evidence from unrecognised source(s): {', '.join(unknown)}",
        observed={"unknown_sources": unknown},
        threshold={"known_sources": sorted(KNOWN_SOURCES)},
    )


def check_freshness(items: Sequence[EvidenceItem], *, now: datetime) -> CheckResult:
    """Evidence must be within its source's cadence and the absolute limit.

    Two thresholds, because they catch different failures: the cadence catches a feed that
    has stopped updating, and the absolute limit catches a source whose cadence is simply
    too slow to answer the question being asked.
    """
    if not items:
        return CheckResult(
            name=CheckName.FRESHNESS,
            passed=False,
            severity=CheckSeverity.BLOCKING,
            detail="no evidence to assess for freshness",
            threshold={"absolute_limit_hours": ABSOLUTE_FRESHNESS_LIMIT.total_seconds() / 3600},
        )

    stale: list[tuple[str, float]] = []
    for item in items:
        age = item.age(now)
        deadline = item.cadence_deadline or ABSOLUTE_FRESHNESS_LIMIT
        limit = min(deadline, ABSOLUTE_FRESHNESS_LIMIT)
        if age > limit:
            stale.append((item.provenance.source, age.total_seconds() / 3600))

    oldest = max(items, key=lambda i: i.age(now))
    oldest_hours = oldest.age(now).total_seconds() / 3600

    if not stale:
        return CheckResult(
            name=CheckName.FRESHNESS,
            passed=True,
            severity=CheckSeverity.INFO,
            detail=f"oldest evidence is {oldest_hours:.1f} h old, within its cadence",
            observed={"oldest_age_hours": round(oldest_hours, 2)},
            threshold={"absolute_limit_hours": ABSOLUTE_FRESHNESS_LIMIT.total_seconds() / 3600},
        )

    worst = max(stale, key=lambda s: s[1])
    return CheckResult(
        name=CheckName.FRESHNESS,
        passed=False,
        severity=CheckSeverity.BLOCKING,
        detail=(
            f"I can't safely answer; nearest reliable data from {worst[0]} is {worst[1]:.1f} h old"
        ),
        observed={"stale": [{"source": s, "age_hours": round(h, 2)} for s, h in sorted(stale)]},
        threshold={"absolute_limit_hours": ABSOLUTE_FRESHNESS_LIMIT.total_seconds() / 3600},
    )


def check_spatial_consistency(
    items: Sequence[EvidenceItem], *, bbox: BoundingBox, tolerance_deg: float = 1.0
) -> CheckResult:
    """Located evidence must actually describe the area being asked about.

    A tolerance is allowed because gridded sources snap to their own cells and a point
    API answers at its nearest node — but evidence a degree outside the box is describing
    a different stretch of sea, and averaging it in would be silently wrong.
    """
    located = [i for i in items if i.lat is not None and i.lon is not None]
    if not located:
        return CheckResult(
            name=CheckName.SPATIAL_CONSISTENCY,
            passed=True,
            severity=CheckSeverity.INFO,
            detail="no located evidence to check",
        )

    outside = [
        {
            "source": i.provenance.source,
            "lat": i.lat,
            "lon": i.lon,
        }
        for i in located
        if not _within(i, bbox, tolerance_deg)
    ]
    if not outside:
        return CheckResult(
            name=CheckName.SPATIAL_CONSISTENCY,
            passed=True,
            severity=CheckSeverity.INFO,
            detail=f"all {len(located)} located item(s) fall within the queried area",
            threshold={"tolerance_deg": tolerance_deg},
        )

    return CheckResult(
        name=CheckName.SPATIAL_CONSISTENCY,
        passed=False,
        severity=CheckSeverity.WARNING,
        detail=(
            f"{len(outside)} evidence item(s) lie more than {tolerance_deg}° outside the "
            "queried area"
        ),
        observed={"outside": outside},
        threshold={"tolerance_deg": tolerance_deg},
    )


def _within(item: EvidenceItem, bbox: BoundingBox, tolerance: float) -> bool:
    assert item.lat is not None and item.lon is not None
    return (
        bbox.min_lat - tolerance <= item.lat <= bbox.max_lat + tolerance
        and bbox.min_lon - tolerance <= item.lon <= bbox.max_lon + tolerance
    )


def check_missing_data(
    items: Sequence[EvidenceItem], *, required_variables: frozenset[str]
) -> CheckResult:
    """Every variable the decision needs must be present with a value."""
    present = {i.variable for i in items if i.variable is not None and i.value is not None}
    missing = sorted(required_variables - present)
    if not missing:
        return CheckResult(
            name=CheckName.MISSING_DATA,
            passed=True,
            severity=CheckSeverity.INFO,
            detail=f"all {len(required_variables)} required variable(s) present",
            observed={"present": sorted(present)},
        )
    return CheckResult(
        name=CheckName.MISSING_DATA,
        passed=False,
        severity=CheckSeverity.BLOCKING,
        detail=f"required variable(s) missing or valueless: {', '.join(missing)}",
        observed={"missing": missing, "present": sorted(present)},
        threshold={"required": sorted(required_variables)},
    )


def check_formula_validity(*, formula_ids: Sequence[str], approved: frozenset[str]) -> CheckResult:
    """Only approved, versioned kernels may contribute to a decision.

    Guards against a result arriving from an unregistered formula — including one an
    upstream component invented. A number without a known formula behind it cannot be
    replayed, so it cannot be trusted.
    """
    unknown = sorted(set(formula_ids) - approved)
    if not unknown:
        return CheckResult(
            name=CheckName.FORMULA_VALIDITY,
            passed=True,
            severity=CheckSeverity.INFO,
            detail=f"all {len(set(formula_ids))} formula(s) are approved",
            observed={"formulas": sorted(set(formula_ids))},
        )
    return CheckResult(
        name=CheckName.FORMULA_VALIDITY,
        passed=False,
        severity=CheckSeverity.BLOCKING,
        detail=f"result produced by unapproved formula(s): {', '.join(unknown)}",
        observed={"unapproved": unknown},
        threshold={"approved": sorted(approved)},
    )


def check_evidence_sufficiency(
    items: Sequence[EvidenceItem], *, minimum: int = MIN_EVIDENCE_ITEMS
) -> CheckResult:
    """There must be enough corroboration to answer at all."""
    count = len(items)
    if count >= minimum:
        return CheckResult(
            name=CheckName.EVIDENCE_SUFFICIENCY,
            passed=True,
            severity=CheckSeverity.INFO,
            detail=f"{count} evidence item(s) available, at or above the minimum of {minimum}",
            observed={"count": count},
            threshold={"minimum": minimum},
        )
    return CheckResult(
        name=CheckName.EVIDENCE_SUFFICIENCY,
        passed=False,
        severity=CheckSeverity.BLOCKING,
        detail=(
            f"only {count} evidence item(s); at least {minimum} are needed for a safety verdict"
        ),
        observed={"count": count},
        threshold={"minimum": minimum},
    )
