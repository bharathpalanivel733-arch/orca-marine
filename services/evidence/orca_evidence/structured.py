"""Structured evidence with ``as_of`` semantics (PLAN.md Phase 3.2).

The observations table is **bitemporal**: every row carries ``valid_time`` (the instant
the value describes) and ``issued_time`` (the instant the publisher released it). Keeping
both is what makes two different questions answerable:

* *"What is the wave height at 06:00 tomorrow?"* — filter on ``valid_time``.
* *"What did we know at 14:00 yesterday, when we told that fisherman it was safe?"* —
  filter on ``issued_time <= as_of``.

The second question is the one that matters for trust. Forecasts are revised constantly;
without ``as_of`` a replay would quietly use a **better** forecast than the one the
original decision saw, and every replay would look correct. Phase 6.4's deterministic
replay depends on this being right.

When several issues cover the same ``(source, variable, valid_time, position)``, the
latest issue **at or before** ``as_of`` wins — never the latest overall.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from orca_schemas import (
    BoundingBox,
    DataQuality,
    EvidenceProvenance,
    MarineVariable,
    MeasurementKind,
    ObservationRecord,
    TimeWindow,
)

# DISTINCT ON picks one row per natural key; the ORDER BY decides which one. Ordering by
# issued_time DESC within the key means "the most recent issue we had at as_of".
AS_OF_SQL = """
SELECT DISTINCT ON (source, variable, valid_time, lat, lon, depth_key)
       variable, value, unit, lat, lon, depth_m, valid_time, issued_time,
       source, dataset_id, quality, kind, license, archive_uri
FROM evidence.observations
WHERE variable = %(variable)s
  AND valid_time >= %(valid_start)s
  AND valid_time <  %(valid_end)s
  AND issued_time <= %(as_of)s
  AND lat BETWEEN %(min_lat)s AND %(max_lat)s
  AND lon BETWEEN %(min_lon)s AND %(max_lon)s
  AND (%(source)s::text IS NULL OR source = %(source)s::text)
ORDER BY source, variable, valid_time, lat, lon, depth_key, issued_time DESC
"""

LATEST_ISSUE_SQL = """
SELECT source, max(issued_time) AS latest_issued
FROM evidence.observations
WHERE variable = %(variable)s AND issued_time <= %(as_of)s
GROUP BY source
ORDER BY latest_issued DESC
"""


@dataclass(frozen=True)
class StructuredEvidence:
    """One observation plus the provenance the trust layer needs."""

    record: ObservationRecord
    provenance: EvidenceProvenance

    @property
    def value(self) -> float | None:
        return self.record.value

    @property
    def variable(self) -> MarineVariable:
        return self.record.variable


class StructuredEvidenceStore:
    """Reads observations with bitemporal (``as_of``) semantics."""

    def __init__(self, connection: Any, *, authority_ranks: dict[str, int] | None = None) -> None:
        self._connection = connection
        # Authority rank belongs to the source, not to each row; the registry is the
        # source of truth and this is the lookup used when building provenance.
        self._authority_ranks = authority_ranks or {}

    def query(
        self,
        *,
        variable: MarineVariable,
        bbox: BoundingBox,
        window: TimeWindow,
        as_of: datetime,
        source: str | None = None,
    ) -> tuple[StructuredEvidence, ...]:
        """Observations valid in ``window``, as known at ``as_of``.

        ``as_of`` is required rather than defaulting to "now": a caller that has not
        thought about which knowledge state it wants is a caller that will produce an
        unreplayable decision.
        """
        if as_of.tzinfo is None:
            msg = "as_of must be timezone-aware"
            raise ValueError(msg)

        with self._connection.cursor() as cursor:
            cursor.execute(
                AS_OF_SQL,
                {
                    "variable": variable.value,
                    "valid_start": window.start,
                    "valid_end": window.end,
                    "as_of": as_of,
                    "min_lat": bbox.min_lat,
                    "max_lat": bbox.max_lat,
                    "min_lon": bbox.min_lon,
                    "max_lon": bbox.max_lon,
                    "source": source,
                },
            )
            rows = cursor.fetchall()

        return tuple(self._to_evidence(row) for row in rows)

    def latest_issue_times(
        self, *, variable: MarineVariable, as_of: datetime
    ) -> tuple[tuple[str, datetime], ...]:
        """Per source, the most recent issue time at or before ``as_of``.

        This is what the freshness gate asks before answering: not "is there data" but
        "how old is the newest thing we had".
        """
        with self._connection.cursor() as cursor:
            cursor.execute(LATEST_ISSUE_SQL, {"variable": variable.value, "as_of": as_of})
            return tuple((row[0], row[1]) for row in cursor.fetchall())

    def _to_evidence(self, row: tuple[Any, ...]) -> StructuredEvidence:
        record = ObservationRecord(
            variable=MarineVariable(row[0]),
            value=float(row[1]) if row[1] is not None else None,
            unit=row[2],
            lat=float(row[3]),
            lon=float(row[4]),
            depth_m=float(row[5]) if row[5] is not None else None,
            valid_time=row[6],
            issued_time=row[7],
            source=row[8],
            dataset_id=row[9],
            quality=DataQuality(row[10]),
            kind=MeasurementKind(row[11]),
            license=row[12],
        )
        provenance = EvidenceProvenance(
            source=record.source,
            url=f"orca://observation/{record.source}/{record.dataset_id or 'default'}",
            issued_time=record.issued_time,
            authority_rank=self._authority_ranks.get(record.source, 5),
            license=record.license,
            provenance_id=(
                f"{record.source}:{record.variable}:{record.valid_time.isoformat()}"
                f":{record.lat:.4f},{record.lon:.4f}"
            ),
            archive_uri=row[13],
        )
        return StructuredEvidence(record=record, provenance=provenance)
