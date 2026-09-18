"""TimescaleDB persistence for observation records (PLAN.md Phase 1.10).

Records land in ``evidence.observations``, a hypertable partitioned on ``valid_time``.
Three design points worth stating, because each one protects a downstream guarantee:

* **Provenance travels with the row.** ``source``, ``dataset_id``, ``issued_time``,
  ``license`` and ``archive_uri`` are columns, not metadata kept elsewhere. A number in
  this table can always answer "where did you come from and when were you issued".
* **Writes are idempotent.** The natural key is
  ``(source, variable, valid_time, lat, lon, depth)``. Re-ingesting the same window —
  which happens constantly with a cadence-aware cache and with replay — updates rather
  than duplicates, so a query cannot silently double-count one forecast.
* **``issued_time`` is never defaulted.** Staleness is computed from it, so a row that
  lied about its issue time would defeat the entire freshness guarantee.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from orca_schemas import ArchiveRef, MarineVariable, ObservationRecord

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "infra" / "db" / "migrations"

UPSERT_SQL = """
INSERT INTO evidence.observations (
    variable, value, unit, lat, lon, depth_m, valid_time, issued_time,
    source, dataset_id, quality, kind, license, archive_uri
) VALUES (
    %(variable)s, %(value)s, %(unit)s, %(lat)s, %(lon)s, %(depth_m)s, %(valid_time)s,
    %(issued_time)s, %(source)s, %(dataset_id)s, %(quality)s, %(kind)s, %(license)s,
    %(archive_uri)s
)
ON CONFLICT (source, variable, valid_time, lat, lon, depth_key)
DO UPDATE SET
    value = EXCLUDED.value,
    unit = EXCLUDED.unit,
    issued_time = EXCLUDED.issued_time,
    quality = EXCLUDED.quality,
    kind = EXCLUDED.kind,
    license = EXCLUDED.license,
    archive_uri = EXCLUDED.archive_uri,
    ingested_at = now()
"""

SELECT_SQL = """
SELECT variable, value, unit, lat, lon, depth_m, valid_time, issued_time,
       source, dataset_id, quality, kind, license
FROM evidence.observations
WHERE variable = %(variable)s
  AND valid_time >= %(start)s AND valid_time < %(end)s
  AND lat BETWEEN %(min_lat)s AND %(max_lat)s
  AND lon BETWEEN %(min_lon)s AND %(max_lon)s
ORDER BY valid_time, lat, lon
"""


def migration_sql() -> str:
    """The schema migration, read from ``infra/db/migrations``."""
    path = MIGRATIONS_DIR / "0001_observations.sql"
    return path.read_text(encoding="utf-8")


class TimescaleObservationStore:
    """Reads and writes observation records.

    Takes an open psycopg connection rather than owning one, so the caller controls
    pooling and transaction scope.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def ensure_schema(self) -> None:
        """Apply the migration. Idempotent — safe to call at startup."""
        with self._connection.cursor() as cursor:
            cursor.execute(migration_sql())
        self._connection.commit()

    def write(
        self, records: Sequence[ObservationRecord], *, archive_ref: ArchiveRef | None = None
    ) -> int:
        """Persist records, updating any that were already ingested.

        Returns the number of rows written.
        """
        if not records:
            return 0

        archive_uri = archive_ref.uri if archive_ref else None
        payload = [
            {
                "variable": record.variable.value,
                "value": record.value,
                "unit": record.unit,
                "lat": record.lat,
                "lon": record.lon,
                "depth_m": record.depth_m,
                "valid_time": record.valid_time,
                "issued_time": record.issued_time,
                "source": record.source,
                "dataset_id": record.dataset_id,
                "quality": record.quality.value,
                "kind": record.kind.value,
                "license": record.license,
                "archive_uri": archive_uri,
            }
            for record in records
        ]
        with self._connection.cursor() as cursor:
            cursor.executemany(UPSERT_SQL, payload)
        self._connection.commit()
        return len(payload)

    def read(
        self,
        *,
        variable: MarineVariable,
        start: datetime,
        end: datetime,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
    ) -> list[ObservationRecord]:
        """Read back records for a variable, box and window."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                SELECT_SQL,
                {
                    "variable": variable.value,
                    "start": start,
                    "end": end,
                    "min_lat": min_lat,
                    "max_lat": max_lat,
                    "min_lon": min_lon,
                    "max_lon": max_lon,
                },
            )
            rows = cursor.fetchall()

        return [
            ObservationRecord(
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
                quality=row[10],
                kind=row[11],
                license=row[12],
            )
            for row in rows
        ]
