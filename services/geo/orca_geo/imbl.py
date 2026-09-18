"""The agreed India-Sri Lanka International Maritime Boundary Line (PLAN.md Phase 2.1).

**This geometry is transcribed from the treaty texts deposited with UN DOALOS. It is not
computed, not a median line, and not derived from EEZ data.** METHODS.md §3 is explicit
that a generated median line would mis-warn fishermen, and Phase 0.4 spike (e) confirmed
that marineregions publishes only EEZ and derived geometries — not this boundary.

Sources (downloaded 2026-09-18, SHA-256 recorded below for audit):

1. **1974 Agreement** — "Agreement between Sri Lanka and India on the Boundary in Historic
   Waters between the two Countries and Related Matters", 26 and 28 June 1974. Article 1
   gives six positions from Palk Strait to Adam's Bridge.
2. **1976 Maritime Boundary Agreement** — 23 March 1976. Article 1 gives thirteen
   positions in the Gulf of Mannar; Article 2 gives eight in the Bay of Bengal.
3. **1976 Supplementary Agreement** — 22 November 1976. Extends the Gulf of Mannar
   boundary from position 13 m to the India-Sri Lanka-Maldives trijunction, Point T.

All three agreements state the boundary is **arcs of great circles** between the listed
positions, so segments are treated as geodesics, not as straight lines in projected space.

**Two continuity checks confirm the three texts describe one boundary**, and both are
asserted in the test suite rather than merely observed here:

* 1976 position ``1m`` (09°06′.0 N, 79°32′.0 E) == 1974 Position 6
* 1976 position ``1b`` (10°05′.0 N, 80°03′.0 E) == 1974 Position 1

**Documented source anomaly.** In the 1976 Maritime Boundary Agreement, Article 1, the
text of position 4 m reads ``08° 40'.0 N 79° 18'.2 N`` — the longitude carries a trailing
``N`` where every other position carries ``E``. This is a transcription error in the
deposited document. It is corrected to ``E`` here because a longitude of "18.2 N" is not a
coordinate at all, and every neighbouring position in the monotonic sequence lies near
79° E. The correction is recorded in :data:`SOURCE_ANOMALIES` and surfaced by
``imbl_provenance()`` so nobody has to rediscover it, and so a reviewer can challenge it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

DOALOS_BASE: Final = "https://www.un.org/depts/los/LEGISLATIONANDTREATIES/PDFFILES/TREATIES/"


@dataclass(frozen=True)
class TreatySource:
    """One deposited agreement, with the checksum of the file that was transcribed."""

    key: str
    title: str
    signed: str
    filename: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{DOALOS_BASE}{self.filename}"


TREATY_SOURCES: Final[tuple[TreatySource, ...]] = (
    TreatySource(
        key="1974BW",
        title=(
            "Agreement between Sri Lanka and India on the Boundary in Historic Waters "
            "between the two Countries and Related Matters"
        ),
        signed="1974-06-26/28",
        filename="LKA-IND1974BW.PDF",
        sha256="2cf10cb60446668fd1125243707d867197c9bfffdbafe407d9fa4a2a23dcd376",
    ),
    TreatySource(
        key="1976MB",
        title=(
            "Agreement between Sri Lanka and India on the Maritime Boundary between the "
            "two Countries in the Gulf of Mannar and the Bay of Bengal and Related Matters"
        ),
        signed="1976-03-23",
        filename="LKA-IND1976MB.PDF",
        sha256="254d1109de73dfdbb66c15056604e7b0fb3c93c3f2c5e23928a7ccf33b4b229f",
    ),
    TreatySource(
        key="1976TP",
        title=(
            "Supplementary Agreement between Sri Lanka and India on the Extension of the "
            "Maritime Boundary in the Gulf of Mannar from Position 13 m to the Trijunction "
            "Point (Point T)"
        ),
        signed="1976-11-22",
        filename="LKA-IND1976TP.PDF",
        sha256="4118dbd721dbce2d231e0d8dbadf7c2c848c8c26e645fada4147f8bbda675826",
    ),
)

SOURCE_ANOMALIES: Final[tuple[str, ...]] = (
    "1976MB Article 1, position 4 m: the deposited text reads \"08° 40'.0 N 79° 18'.2 N\"; "
    "the longitude hemisphere is a typo for E. Corrected to E here — a longitude of "
    "'18.2 N' is not a coordinate, and the neighbouring positions (3 m at 79°29'.3 E, "
    "5 m at 79°13'.0 E) bracket it on a monotonic run down the Gulf of Mannar.",
)


def dm(degrees: int, minutes: float) -> float:
    """Degrees and decimal minutes -> decimal degrees.

    The treaties state positions as e.g. ``09° 40.15' N``; this is the only conversion
    applied to them. No rounding, no projection, no smoothing.
    """
    if minutes < 0 or minutes >= 60:
        msg = f"minutes must be in [0, 60), got {minutes}"
        raise ValueError(msg)
    return degrees + minutes / 60.0


@dataclass(frozen=True)
class BoundaryPosition:
    """One treaty-defined position on the boundary."""

    label: str
    lat: float
    lon: float
    treaty: str
    article: str

    @property
    def lonlat(self) -> tuple[float, float]:
        """(lon, lat), the order PostGIS and GeoJSON expect."""
        return (self.lon, self.lat)


# --- 1974 Agreement, Article 1: Palk Strait to Adam's Bridge -------------------------
PALK_STRAIT_1974: Final[tuple[BoundaryPosition, ...]] = (
    BoundaryPosition("1", dm(10, 5.0), dm(80, 3.0), "1974BW", "Article 1"),
    BoundaryPosition("2", dm(9, 57.0), dm(79, 35.0), "1974BW", "Article 1"),
    BoundaryPosition("3", dm(9, 40.15), dm(79, 22.60), "1974BW", "Article 1"),
    BoundaryPosition("4", dm(9, 21.80), dm(79, 30.70), "1974BW", "Article 1"),
    BoundaryPosition("5", dm(9, 13.0), dm(79, 32.0), "1974BW", "Article 1"),
    BoundaryPosition("6", dm(9, 6.0), dm(79, 32.0), "1974BW", "Article 1"),
)

# --- 1976 Agreement, Article 1: Gulf of Mannar ---------------------------------------
GULF_OF_MANNAR_1976: Final[tuple[BoundaryPosition, ...]] = (
    BoundaryPosition("1m", dm(9, 6.0), dm(79, 32.0), "1976MB", "Article 1"),
    BoundaryPosition("2m", dm(9, 0.0), dm(79, 31.3), "1976MB", "Article 1"),
    BoundaryPosition("3m", dm(8, 53.8), dm(79, 29.3), "1976MB", "Article 1"),
    # Longitude hemisphere corrected from 'N' to 'E'; see SOURCE_ANOMALIES.
    BoundaryPosition("4m", dm(8, 40.0), dm(79, 18.2), "1976MB", "Article 1"),
    BoundaryPosition("5m", dm(8, 37.2), dm(79, 13.0), "1976MB", "Article 1"),
    BoundaryPosition("6m", dm(8, 31.2), dm(79, 4.7), "1976MB", "Article 1"),
    BoundaryPosition("7m", dm(8, 22.2), dm(78, 55.4), "1976MB", "Article 1"),
    BoundaryPosition("8m", dm(8, 12.2), dm(78, 53.7), "1976MB", "Article 1"),
    BoundaryPosition("9m", dm(7, 35.3), dm(78, 45.7), "1976MB", "Article 1"),
    BoundaryPosition("10m", dm(7, 21.0), dm(78, 38.8), "1976MB", "Article 1"),
    BoundaryPosition("11m", dm(6, 30.8), dm(78, 12.2), "1976MB", "Article 1"),
    BoundaryPosition("12m", dm(5, 53.9), dm(77, 50.7), "1976MB", "Article 1"),
    BoundaryPosition("13m", dm(5, 0.0), dm(77, 10.6), "1976MB", "Article 1"),
)

# --- 1976 Supplementary Agreement, Article 1: 13 m to the trijunction ----------------
TRIJUNCTION_EXTENSION_1976: Final[tuple[BoundaryPosition, ...]] = (
    BoundaryPosition("T", dm(4, 47.04), dm(77, 1.40), "1976TP", "Article 1"),
)

# --- 1976 Agreement, Article 2: Bay of Bengal ----------------------------------------
BAY_OF_BENGAL_1976: Final[tuple[BoundaryPosition, ...]] = (
    BoundaryPosition("1b", dm(10, 5.0), dm(80, 3.0), "1976MB", "Article 2"),
    BoundaryPosition("1ba", dm(10, 5.8), dm(80, 5.0), "1976MB", "Article 2"),
    BoundaryPosition("1bb", dm(10, 8.4), dm(80, 9.5), "1976MB", "Article 2"),
    BoundaryPosition("2b", dm(10, 33.0), dm(80, 46.0), "1976MB", "Article 2"),
    BoundaryPosition("3b", dm(10, 41.7), dm(81, 2.5), "1976MB", "Article 2"),
    BoundaryPosition("4b", dm(11, 2.7), dm(81, 56.0), "1976MB", "Article 2"),
    BoundaryPosition("5b", dm(11, 16.0), dm(82, 24.4), "1976MB", "Article 2"),
    BoundaryPosition("6b", dm(11, 26.6), dm(83, 22.0), "1976MB", "Article 2"),
)


def imbl_positions() -> tuple[BoundaryPosition, ...]:
    """The complete boundary as one ordered run, north-east to south-west.

    Order: Bay of Bengal (6b down to 1bb/1ba) -> the shared 1974 Position 1 / 1b ->
    Palk Strait (1974 Positions 2-5) -> the shared Position 6 / 1m -> Gulf of Mannar
    (2m-13m) -> Point T.

    The two shared positions appear once, not twice: they are the same point on the sea,
    and duplicating them would put a zero-length segment into the geometry.
    """
    bay_reversed = tuple(reversed(BAY_OF_BENGAL_1976))  # 6b ... 1bb, 1ba, 1b
    palk_after_first = PALK_STRAIT_1974[1:]  # positions 2..6 (1b == Position 1)
    mannar_after_first = GULF_OF_MANNAR_1976[1:]  # 2m..13m (Position 6 == 1m)
    return bay_reversed + palk_after_first + mannar_after_first + TRIJUNCTION_EXTENSION_1976


def imbl_linestring_wkt() -> str:
    """The boundary as WKT ``LINESTRING`` in EPSG:4326 (lon lat order)."""
    coordinates = ", ".join(f"{p.lon:.6f} {p.lat:.6f}" for p in imbl_positions())
    return f"LINESTRING({coordinates})"


def imbl_provenance() -> dict[str, object]:
    """Everything needed to audit or challenge this geometry.

    Attached to any warning derived from the boundary, so a fisherman's advisory can be
    traced to the treaty article it rests on.
    """
    return {
        "name": "India-Sri Lanka International Maritime Boundary Line",
        "derivation": "transcribed from deposited treaty texts; NOT a computed median line",
        "segment_interpolation": (
            "arcs of great circles (geodesic), per Article 1 of each agreement"
        ),
        "crs": "EPSG:4326",
        "position_count": len(imbl_positions()),
        "sources": [
            {
                "key": s.key,
                "title": s.title,
                "signed": s.signed,
                "url": s.url,
                "sha256": s.sha256,
            }
            for s in TREATY_SOURCES
        ],
        "anomalies": list(SOURCE_ANOMALIES),
        "retrieved": "2026-09-18",
    }
