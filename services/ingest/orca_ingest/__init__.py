"""ORCA data ingestion.

Phase 1.1 defines the adapter interface and the common record contract every source
speaks. The concrete adapters (INCOIS ERDDAP, IMD, CMEMS, Open-Meteo, PFZ, MOSDAC, OMNI
buoys) and the degradation chain follow in PLAN.md Phase 1.2 onward.

No source is contacted from this package yet, and none is configured as live.
"""

from orca_schemas import RunContext

from orca_ingest.adapter import (
    FetchRequest,
    FetchResult,
    SourceAdapter,
    SourceUnavailableError,
)

SERVICE_NAME = "orca-ingest"
__version__ = "0.1.0"


def new_run_context() -> RunContext:
    """Start a run owned by the ingest service."""
    return RunContext(service=SERVICE_NAME)


__all__ = [
    "SERVICE_NAME",
    "FetchRequest",
    "FetchResult",
    "SourceAdapter",
    "SourceUnavailableError",
    "__version__",
    "new_run_context",
]
