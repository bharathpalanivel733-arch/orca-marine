"""ORCA data ingestion.

Phase 1 delivers the adapter interface, the common record contract, source adapters, the
explicit degradation policy, cadence-aware caching and persistence.

Two adapters reach live data today: Open-Meteo Marine (keyless) and INCOIS ERDDAP (whose
datasets turned out to be historical archives, not live feeds). Every other source is a
scaffold that degrades with a stated reason — credentials, portal access, or a
bulletin-only publication format. Nothing fabricates a value when a source is
unavailable.
"""

from orca_schemas import RunContext

from orca_ingest.adapter import (
    FetchRequest,
    FetchResult,
    SourceAdapter,
    SourceUnavailableError,
)
from orca_ingest.archive import (
    FilesystemArchive,
    NullArchive,
    RawPayloadArchive,
    S3Archive,
)
from orca_ingest.cache import CacheEntry, CadenceCache, InMemoryCacheBackend, cache_key
from orca_ingest.degradation import ResilientIngestor, ResolvedFetch, SourceRegistry
from orca_ingest.http import HttpError, HttpFetcher, RawResponse

SERVICE_NAME = "orca-ingest"
__version__ = "0.1.0"


def new_run_context() -> RunContext:
    """Start a run owned by the ingest service."""
    return RunContext(service=SERVICE_NAME)


__all__ = [
    "SERVICE_NAME",
    "CacheEntry",
    "CadenceCache",
    "FetchRequest",
    "FetchResult",
    "FilesystemArchive",
    "HttpError",
    "HttpFetcher",
    "InMemoryCacheBackend",
    "NullArchive",
    "RawPayloadArchive",
    "RawResponse",
    "ResilientIngestor",
    "ResolvedFetch",
    "S3Archive",
    "SourceAdapter",
    "SourceRegistry",
    "SourceUnavailableError",
    "__version__",
    "cache_key",
    "new_run_context",
]
