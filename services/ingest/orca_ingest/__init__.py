"""ORCA data ingestion.

Phase 0 establishes the package and its shared-contract wiring only. The source
adapters (INCOIS ERDDAP, IMD, CMEMS, Open-Meteo, PFZ, MOSDAC, OMNI buoys), the
common record contract and the degradation chain arrive in PLAN.md Phase 1.

No source is contacted from this package yet, and none is configured as live.
"""

from orca_schemas import RunContext

SERVICE_NAME = "orca-ingest"
__version__ = "0.1.0"


def new_run_context() -> RunContext:
    """Start a run owned by the ingest service."""
    return RunContext(service=SERVICE_NAME)


__all__ = ["SERVICE_NAME", "__version__", "new_run_context"]
