"""Spatial and seasonal constraints for the reasoning solver (PLAN.md Phase 2.5).

Sustainability is a scored SIH dimension and, more to the point, a real obligation: a
recommendation that sends a boat into a no-take marine protected area or through a
state's monsoon fishing ban is a bad recommendation however good the catch would be.

Constraints are modelled as **hard or soft**, and the distinction is load-bearing:

* **Hard** — a no-take MPA boundary, a statutory ban period, the IMBL itself. The solver
  must not return a plan that violates one. These are legal limits, not preferences.
* **Soft** — a seasonal advisory, a voluntary conservation zone. Violating one costs the
  plan points; it does not disqualify it.

A constraint evaluates to a violation or not for a given position and time. Nothing here
scores, ranks or trades off — that is the Pareto kernel's job in Phase 4.3. This module
answers one question honestly: *is this allowed, and if not, under what rule?*
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class ConstraintSeverity(StrEnum):
    """Whether a constraint disqualifies a plan or merely penalises it."""

    HARD = "hard"
    SOFT = "soft"


class ConstraintKind(StrEnum):
    """What sort of restriction this is, for explanation and UI grouping."""

    MARITIME_BOUNDARY = "maritime_boundary"
    MARINE_PROTECTED_AREA = "marine_protected_area"
    SEASONAL_BAN = "seasonal_ban"
    RESTRICTED_ZONE = "restricted_zone"


@dataclass(frozen=True)
class SeasonalWindow:
    """An annually recurring closed season, e.g. a monsoon fishing ban.

    Stored as month/day so it repeats each year, and handles windows that wrap the new
    year (a December-to-February closure) rather than silently failing on them.
    """

    start_month: int
    start_day: int
    end_month: int
    end_day: int

    def __post_init__(self) -> None:
        for month in (self.start_month, self.end_month):
            if not 1 <= month <= 12:
                msg = f"month must be 1-12, got {month}"
                raise ValueError(msg)
        for day in (self.start_day, self.end_day):
            if not 1 <= day <= 31:
                msg = f"day must be 1-31, got {day}"
                raise ValueError(msg)

    def contains(self, moment: date) -> bool:
        """Whether a date falls inside the closed season."""
        start = (self.start_month, self.start_day)
        end = (self.end_month, self.end_day)
        current = (moment.month, moment.day)
        if start <= end:
            return start <= current <= end
        # Wraps the year end.
        return current >= start or current <= end


@dataclass(frozen=True)
class ConstraintViolation:
    """A specific rule that a position or plan breaks."""

    constraint_id: str
    name: str
    kind: ConstraintKind
    severity: ConstraintSeverity
    detail: str
    authority: str
    citation: str | None = None

    @property
    def blocks_plan(self) -> bool:
        return self.severity is ConstraintSeverity.HARD


@dataclass(frozen=True)
class ProtectedArea:
    """A spatially defined restriction, evaluated against PostGIS geometry.

    ``geometry_wkt`` is the authoritative shape. It is held rather than approximated,
    because a protected-area boundary drawn by eye is worse than none: it would give
    confident wrong answers near exactly the edge where the answer matters.
    """

    area_id: str
    name: str
    kind: ConstraintKind
    severity: ConstraintSeverity
    authority: str
    geometry_wkt: str
    citation: str | None = None
    closed_seasons: tuple[SeasonalWindow, ...] = field(default_factory=tuple)

    def is_closed_on(self, moment: date) -> bool:
        """Whether the area is closed on a date.

        An area with no seasonal windows is closed year-round — a no-take MPA is not
        seasonal — so an empty season list means "always", never "never".
        """
        if not self.closed_seasons:
            return True
        return any(window.contains(moment) for window in self.closed_seasons)

    def violation(self, detail: str) -> ConstraintViolation:
        return ConstraintViolation(
            constraint_id=self.area_id,
            name=self.name,
            kind=self.kind,
            severity=self.severity,
            detail=detail,
            authority=self.authority,
            citation=self.citation,
        )


@dataclass(frozen=True)
class SeasonalBan:
    """A time-based ban that applies to a named jurisdiction.

    India's east- and west-coast monsoon bans run on different dates and are set by state
    notifications, so the dates are configuration loaded from reference data — never
    hardcoded here, where they would rot silently and wrongly.
    """

    ban_id: str
    name: str
    jurisdiction: str
    window: SeasonalWindow
    authority: str
    severity: ConstraintSeverity = ConstraintSeverity.HARD
    citation: str | None = None

    def evaluate(self, moment: date) -> ConstraintViolation | None:
        """Whether this ban is in force on a date."""
        if not self.window.contains(moment):
            return None
        return ConstraintViolation(
            constraint_id=self.ban_id,
            name=self.name,
            kind=ConstraintKind.SEASONAL_BAN,
            severity=self.severity,
            detail=f"{self.name} is in force in {self.jurisdiction} on {moment.isoformat()}",
            authority=self.authority,
            citation=self.citation,
        )


class ConstraintSet:
    """The constraints in force for a plan.

    Spatial containment is delegated to PostGIS (``storage.py``); this class composes the
    results with the time-based rules so that a caller gets one answer covering both.
    """

    def __init__(
        self,
        *,
        areas: tuple[ProtectedArea, ...] = (),
        bans: tuple[SeasonalBan, ...] = (),
    ) -> None:
        self._areas = areas
        self._bans = bans

    @property
    def areas(self) -> tuple[ProtectedArea, ...]:
        return self._areas

    @property
    def bans(self) -> tuple[SeasonalBan, ...]:
        return self._bans

    def evaluate_temporal(
        self, moment: date, jurisdictions: frozenset[str]
    ) -> tuple[ConstraintViolation, ...]:
        """Seasonal bans in force for the given jurisdictions on a date."""
        violations = [
            violation
            for ban in self._bans
            if ban.jurisdiction in jurisdictions and (violation := ban.evaluate(moment)) is not None
        ]
        return tuple(violations)

    def evaluate_areas(
        self, containing_area_ids: frozenset[str], moment: date
    ) -> tuple[ConstraintViolation, ...]:
        """Violations for areas the position falls inside.

        ``containing_area_ids`` comes from a PostGIS containment query, so the geometry
        test is done by PostGIS rather than approximated in Python.
        """
        violations = []
        for area in self._areas:
            if area.area_id not in containing_area_ids:
                continue
            if not area.is_closed_on(moment):
                continue
            violations.append(
                area.violation(f"position lies inside {area.name}, closed on {moment.isoformat()}")
            )
        return tuple(violations)

    @staticmethod
    def blocking(violations: tuple[ConstraintViolation, ...]) -> tuple[ConstraintViolation, ...]:
        """Only the violations that disqualify a plan."""
        return tuple(v for v in violations if v.blocks_plan)
