"""Source adapters (PLAN.md Phase 1.2-1.9).

Working, verified live:
  * ``OpenMeteoMarineAdapter``  - keyless wave/swell forecasts
  * ``IncoisErddapAdapter``     - INCOIS ERDDAP archives, with live dimension discovery

Scaffolds that degrade cleanly with a stated reason (credential- or portal-gated, or
published only as bulletins): IMD, CMEMS, MOSDAC, INCOIS PFZ, INCOIS OSF, NIOT OMNI.
"""

from orca_ingest.adapters.credentialed import (
    CmemsAdapter,
    CredentialedAdapter,
    ImdAdapter,
    IncoisOsfAdapter,
    IncoisPfzAdapter,
    MosdacAdapter,
    NiotOmniBuoyAdapter,
)
from orca_ingest.adapters.incois_erddap import DatasetMetadata, IncoisErddapAdapter
from orca_ingest.adapters.open_meteo import OpenMeteoMarineAdapter

__all__ = [
    "CmemsAdapter",
    "CredentialedAdapter",
    "DatasetMetadata",
    "ImdAdapter",
    "IncoisErddapAdapter",
    "IncoisOsfAdapter",
    "IncoisPfzAdapter",
    "MosdacAdapter",
    "NiotOmniBuoyAdapter",
    "OpenMeteoMarineAdapter",
]
