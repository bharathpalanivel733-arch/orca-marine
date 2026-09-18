"""Reference-data ingestion paths (PLAN.md Phase 2.1).

Phase 2 needs six geometry layers. They differ enormously in how obtainable they are, and
pretending otherwise would hide real work:

============================  ==================================================
layer                         status
============================  ==================================================
India-Sri Lanka IMBL          **LOADED** — transcribed from the deposited treaties
EEZ (marineregions v12)       **IMPLEMENTED** — public WFS, fetched on demand
MPA (WDPA / Protected Planet) documented path; bulk download, monthly releases
Seasonal fishing bans         documented path; per-state notifications, manual
Coastline (OSM / NE)          documented path; bulk download
Bathymetry (GEBCO)            documented path; bulk raster download
============================  ==================================================

Each entry below carries the real endpoint, the access method and the licence, so the
work left is specified rather than vague. A loader that is not implemented raises and
says what to do — it never returns an empty layer that a caller could mistake for
"there is nothing there", which for a protected-area layer would be a dangerous lie.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AccessMethod(StrEnum):
    """How a layer is obtained."""

    TRANSCRIBED = "transcribed"
    WFS = "wfs"
    BULK_DOWNLOAD = "bulk_download"
    MANUAL = "manual"


class LayerStatus(StrEnum):
    """Whether ORCA can load the layer today."""

    LOADED = "loaded"
    IMPLEMENTED = "implemented"
    DOCUMENTED = "documented"


@dataclass(frozen=True)
class ReferenceLayer:
    """One geometry layer ORCA depends on."""

    layer_id: str
    name: str
    authority: str
    access: AccessMethod
    status: LayerStatus
    endpoint: str
    license: str
    notes: str


REFERENCE_LAYERS: tuple[ReferenceLayer, ...] = (
    ReferenceLayer(
        layer_id="imbl_ind_lka",
        name="India-Sri Lanka International Maritime Boundary Line",
        authority="UN DOALOS deposited treaty texts (1974, 1976, 1976 supplementary)",
        access=AccessMethod.TRANSCRIBED,
        status=LayerStatus.LOADED,
        endpoint="https://www.un.org/depts/los/LEGISLATIONANDTREATIES/PDFFILES/TREATIES/",
        license="UN document; treaty text",
        notes=(
            "Transcribed into orca_geo.imbl with per-file SHA-256. NOT a median line. "
            "Segments are arcs of great circles per Article 1 of each agreement."
        ),
    ),
    ReferenceLayer(
        layer_id="eez",
        name="Exclusive Economic Zones v12",
        authority="Flanders Marine Institute (VLIZ) / marineregions.org",
        access=AccessMethod.WFS,
        status=LayerStatus.IMPLEMENTED,
        endpoint="https://geo.vliz.be/geoserver/MarineRegions/wfs",
        license="CC-BY 4.0 (attribution to VLIZ required)",
        notes=(
            "Layer MarineRegions:eez. Context only — the EEZ is NOT the IMBL and must "
            "never be substituted for it in a boundary warning."
        ),
    ),
    ReferenceLayer(
        layer_id="mpa_wdpa",
        name="World Database on Protected Areas (marine)",
        authority="UNEP-WCMC / IUCN, via Protected Planet",
        access=AccessMethod.BULK_DOWNLOAD,
        status=LayerStatus.DOCUMENTED,
        endpoint="https://www.protectedplanet.net/en/thematic-areas/wdpa",
        license="WDPA terms of use; attribution and no-redistribution conditions apply",
        notes=(
            "Monthly release, downloaded as a country or global shapefile/geopackage and "
            "loaded into geo.protected_areas. Filter to marine designations. Licence "
            "restricts redistribution, so the file is not vendored into this repo."
        ),
    ),
    ReferenceLayer(
        layer_id="seasonal_bans",
        name="State monsoon fishing bans",
        authority="Indian coastal state fisheries departments",
        access=AccessMethod.MANUAL,
        status=LayerStatus.DOCUMENTED,
        endpoint="per-state fisheries department notifications",
        license="government notification",
        notes=(
            "Dates differ by state and coast and are revised by notification, so they are "
            "loaded into geo.seasonal_bans as reference data with their citation. They "
            "must never be hardcoded: a stale ban date in code would be both a legal and "
            "a sustainability error."
        ),
    ),
    ReferenceLayer(
        layer_id="coastline",
        name="Coastline",
        authority="OpenStreetMap / Natural Earth",
        access=AccessMethod.BULK_DOWNLOAD,
        status=LayerStatus.DOCUMENTED,
        endpoint="https://osmdata.openstreetmap.de/data/coastlines.html",
        license="ODbL (OSM) / public domain (Natural Earth)",
        notes="Used for landfall checks and as a hard constraint on the route cost surface.",
    ),
    ReferenceLayer(
        layer_id="bathymetry_gebco",
        name="GEBCO global bathymetry grid",
        authority="GEBCO / BODC",
        access=AccessMethod.BULK_DOWNLOAD,
        status=LayerStatus.DOCUMENTED,
        endpoint="https://www.gebco.net/data_and_products/gridded_bathymetry_data/",
        license="GEBCO grid terms (free use with attribution)",
        notes=(
            "A raster, so tiles are referenced in geo.bathymetry_tiles and sampled from "
            "object storage rather than stored in the table."
        ),
    ),
)


def layer(layer_id: str) -> ReferenceLayer:
    """Look up one layer's ingestion path."""
    for entry in REFERENCE_LAYERS:
        if entry.layer_id == layer_id:
            return entry
    known = ", ".join(sorted(e.layer_id for e in REFERENCE_LAYERS))
    msg = f"unknown reference layer {layer_id!r}; known layers: {known}"
    raise KeyError(msg)


def eez_wfs_url(*, sovereigns: tuple[str, ...] = ("India", "Sri Lanka"), limit: int = 10) -> str:
    """Build the marineregions WFS request for the EEZs ORCA needs.

    Returned as a URL rather than fetched here so the caller owns the HTTP client, its
    TLS context and its archiving — the same discipline the ingest adapters follow.
    """
    filters = " OR ".join(f"sovereign1='{name}'" for name in sovereigns)
    return (
        "https://geo.vliz.be/geoserver/MarineRegions/wfs"
        "?service=WFS&version=2.0.0&request=GetFeature"
        "&typeName=MarineRegions:eez&outputFormat=application/json"
        f"&count={limit}&CQL_FILTER={filters}"
    )


def load_unimplemented(layer_id: str) -> Any:
    """Raise with the specific next step for a layer that is documented but not built.

    Deliberately raises rather than returning an empty layer: an empty protected-area
    layer would read as "no restrictions here", which is exactly the wrong answer.
    """
    entry = layer(layer_id)
    msg = (
        f"{entry.name} is not loaded. Access: {entry.access} from {entry.endpoint}. "
        f"Licence: {entry.license}. {entry.notes}"
    )
    raise NotImplementedError(msg)
