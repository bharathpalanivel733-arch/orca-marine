"""ORCA geospatial service (PLAN.md Phase 2).

Geofencing against the **agreed 1974/1976 India-Sri Lanka maritime boundary**, transcribed
from the treaty texts deposited with UN DOALOS. No median line is computed anywhere in
this package; METHODS.md section 3 is explicit that a generated median would mis-warn
fishermen, and the 2025 arrests make that a liberty issue, not a technical nicety.

Contents:
  * ``imbl``        - the treaty geometry, with provenance and source checksums
  * ``geofence``    - distance, side, and configurable amber/red warning bands
  * ``drift``       - predictive drift and time-to-boundary
  * ``constraints`` - MPA and seasonal-ban rules for the solver
  * ``storage``     - PostGIS loading and authoritative spatial queries
"""

from orca_geo.constraints import (
    ConstraintKind,
    ConstraintSet,
    ConstraintSeverity,
    ConstraintViolation,
    ProtectedArea,
    SeasonalBan,
    SeasonalWindow,
)
from orca_geo.drift import DriftForecast, DriftPredictor, VesselMotion
from orca_geo.geofence import (
    BandThresholds,
    BoundaryProximity,
    BoundarySide,
    ImblGeofence,
    WarningBand,
)
from orca_geo.imbl import imbl_linestring_wkt, imbl_positions, imbl_provenance
from orca_geo.storage import GeoStore

SERVICE_NAME = "orca-geo"
__version__ = "0.1.0"

__all__ = [
    "SERVICE_NAME",
    "BandThresholds",
    "BoundaryProximity",
    "BoundarySide",
    "ConstraintKind",
    "ConstraintSet",
    "ConstraintSeverity",
    "ConstraintViolation",
    "DriftForecast",
    "DriftPredictor",
    "GeoStore",
    "ImblGeofence",
    "ProtectedArea",
    "SeasonalBan",
    "SeasonalWindow",
    "VesselMotion",
    "WarningBand",
    "__version__",
    "imbl_linestring_wkt",
    "imbl_positions",
    "imbl_provenance",
]
