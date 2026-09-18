"""Client scaffolds for credential- and portal-gated sources.

These are deliberately **scaffolds, not working integrations**, and each one says so at
runtime. They exist so the registry, the degradation chain and the provenance trail can
carry them today — a source that is unavailable for a stated reason is a first-class
outcome in this system (``AttemptOutcome.UNAVAILABLE``), not an omission.

Every class here raises :class:`SourceUnavailableError` with a specific, actionable
reason when it cannot work. None of them fabricates a record, returns a placeholder
value, or silently succeeds. A fetch that cannot happen must be visible as a fetch that
did not happen, because the evidence-sufficiency gate (PLAN.md Phase 6.3) has to be able
to see the hole.

Verified status as of 2026-09-18:

* **IMD** (``api.imd.gov.in``) — *now requires an API key.* Every endpoint returns
  ``401 {"error":"API key missing"}``. DEPLOYMENT.md §2 records it as keyless; that is
  no longer true and the doc needs correcting.
* **CMEMS** — free account, credentials via the ``copernicusmarine`` toolbox.
* **MOSDAC** — SSO registration, ``mdapi`` client, 3-day L1 latency for general users.
* **INCOIS PFZ / OSF** — no clean JSON API; text bulletins and WMS layers only.
* **NIOT OMNI buoys** — account-gated OMNI-RAMA portal.
"""

from __future__ import annotations

import os
from datetime import timedelta

from orca_schemas import BoundingBox, Cadence, MarineVariable, SourceDescriptor

from orca_ingest.adapter import FetchRequest, FetchResult, SourceAdapter, SourceUnavailableError
from orca_ingest.archive import NullArchive, RawPayloadArchive
from orca_ingest.http import HttpFetcher

# The Indian Ocean box ORCA operates in (DEPLOYMENT.md griddap examples).
INDIAN_OCEAN = BoundingBox(min_lat=-10, max_lat=30, min_lon=45, max_lon=100)


class CredentialedAdapter(SourceAdapter):
    """Base for sources that cannot run without credentials or manual access.

    Subclasses declare which environment variables they need. ``fetch`` refuses with a
    precise reason when they are absent, and refuses with a different, equally precise
    reason when they are present but the integration is not built yet. The two cases are
    never conflated, because "you have not registered" and "we have not written it" call
    for different actions from the team.
    """

    required_env: tuple[str, ...] = ()
    integration_status: str = "not implemented"

    def __init__(
        self,
        *,
        fetcher: HttpFetcher | None = None,
        archive: RawPayloadArchive | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._archive = archive or NullArchive()
        self._env = env if env is not None else dict(os.environ)

    def missing_credentials(self) -> tuple[str, ...]:
        """Required environment variables that are absent or blank."""
        return tuple(name for name in self.required_env if not self._env.get(name, "").strip())

    @property
    def credentials_available(self) -> bool:
        return not self.missing_credentials()

    async def fetch(self, request: FetchRequest) -> FetchResult:
        missing = self.missing_credentials()
        if missing:
            raise SourceUnavailableError(
                self.descriptor.source_id,
                f"missing credentials: {', '.join(missing)} (see .env.example)",
            )
        raise SourceUnavailableError(
            self.descriptor.source_id,
            f"credentials present but the client is {self.integration_status}",
        )


class ImdAdapter(CredentialedAdapter):
    """IMD marine bulletins, cyclone tracks and warnings.

    **Live finding (2026-09-18): this API is no longer keyless.** Every endpoint —
    ``seabulletin``, ``coastalbulletin``, ``portwarning``, ``fishermen-warning``,
    ``cyclone_track``, ``cyclone_wind``, ``cyclone_cou``, ``current_wx`` — returns
    ``401 {"error":"API key missing"}``. The project docs describe it as requiring no
    key, and that must be corrected before the finale rather than discovered on stage.

    Note also that most IMD marine products are *bulletins*, not gridded numbers: they
    belong in the RAG corpus (Phase 3.3) and the alert subsystem (Phase 8), not
    necessarily in ``ObservationRecord`` form.
    """

    required_env = ("IMD_API_KEY",)
    integration_status = "not implemented (Phase 8 alert subsystem owns the bulletin path)"

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="imd",
            name="India Meteorological Department API",
            authority_rank=1,
            variables=frozenset({MarineVariable.WIND_SPEED, MarineVariable.WIND_DIRECTION}),
            coverage=INDIAN_OCEAN,
            cadence=Cadence(period=timedelta(hours=6), grace=timedelta(hours=2)),
            license="IMD terms; attribution and client-side caching requested",
            requires_auth=True,
            attribution="India Meteorological Department (IMD)",
        )


class CmemsAdapter(CredentialedAdapter):
    """Copernicus Marine (CMEMS) — the primary wave source.

    Phase 0.4 spike (b) established that INCOIS publishes no searchable wave/OSF griddap
    dataset, so ``cmems_mod_glo_wav_anfc_0.083deg_PT3H-i`` (VHM0/VTPK/VMDR) is ORCA's
    **primary** wave source rather than a fallback.

    Access is through the ``copernicusmarine`` Python toolbox with a free account, not a
    plain REST call, so this scaffold stops at credential checking. Dataset ids rotate
    roughly twice a year — ``copernicusmarine describe`` must be re-run before the
    finale, and the id must not be hardcoded anywhere until it has been verified live.
    """

    required_env = ("CMEMS_USERNAME", "CMEMS_PASSWORD")
    integration_status = (
        "not implemented (needs the copernicusmarine toolbox and a verified dataset id)"
    )

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="cmems",
            name="Copernicus Marine Service",
            authority_rank=2,
            variables=frozenset(
                {
                    MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
                    MarineVariable.PEAK_WAVE_PERIOD,
                    MarineVariable.MEAN_WAVE_DIRECTION,
                    MarineVariable.SWELL_HEIGHT,
                    MarineVariable.CURRENT_SPEED,
                    MarineVariable.CURRENT_DIRECTION,
                    MarineVariable.SEA_SURFACE_TEMPERATURE,
                    MarineVariable.CHLOROPHYLL,
                }
            ),
            coverage=BoundingBox(min_lat=-90, max_lat=90, min_lon=-180, max_lon=180),
            cadence=Cadence(period=timedelta(hours=12), grace=timedelta(hours=4)),
            license="Copernicus Marine Service licence (free, attribution required)",
            requires_auth=True,
            attribution="E.U. Copernicus Marine Service Information",
        )


class MosdacAdapter(CredentialedAdapter):
    """ISRO MOSDAC — Oceansat-3 OCM-3 chlorophyll and SCAT-3 winds.

    Enhancement only: general users get L1 with a 3-day latency, which is useless for a
    safety verdict but fine for the causal and climatology work.

    **Oceansat-3 SST is deliberately absent from this descriptor.** The EOS-06 SSTM has a
    scan-mechanism fault and is not operational (METHODS.md §3); claiming it would be an
    easy way to lose credibility with an ISRO jury.
    """

    required_env = ("MOSDAC_USERNAME", "MOSDAC_PASSWORD")
    integration_status = "not implemented (needs the mdapi client and SSO registration)"

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="mosdac",
            name="ISRO MOSDAC",
            authority_rank=2,
            variables=frozenset({MarineVariable.CHLOROPHYLL, MarineVariable.WIND_SPEED}),
            coverage=INDIAN_OCEAN,
            cadence=Cadence(period=timedelta(days=1), grace=timedelta(days=3)),
            license="MOSDAC/ISRO data policy; registration required",
            requires_auth=True,
            attribution="ISRO / MOSDAC",
        )


class IncoisPfzAdapter(CredentialedAdapter):
    """INCOIS Potential Fishing Zone advisories.

    Issued three times a week, published as text bulletins and WMS layers with **no clean
    JSON API**, so ingestion means parsing per coastal node (~1,223 nodes).

    PFZ is a *probability-of-aggregation indicator* derived from SST fronts and
    chlorophyll — not "where the fish are" (METHODS.md §3). Whatever consumes this must
    carry the advisory's age and that framing with it.
    """

    required_env = ()
    integration_status = "not implemented (text/WMS parser, Phase 1.6)"

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="incois_pfz",
            name="INCOIS Potential Fishing Zone advisories",
            authority_rank=1,
            variables=frozenset(),
            coverage=INDIAN_OCEAN,
            # Three times a week: ~56 h between issues, with generous grace.
            cadence=Cadence(period=timedelta(hours=56), grace=timedelta(hours=12)),
            license="INCOIS advisory terms",
            requires_auth=False,
            attribution="INCOIS Marine Fisheries Advisory Service",
        )

    async def fetch(self, request: FetchRequest) -> FetchResult:
        raise SourceUnavailableError(
            "incois_pfz",
            "PFZ advisories have no JSON API; the text/WMS parser is Phase 1.6 and is not built",
        )


class IncoisOsfAdapter(CredentialedAdapter):
    """INCOIS Ocean State Forecast (INDOFOS).

    Significant wave height, swell, currents, SST, mixed-layer depth, D20 and tides at
    3-hour steps over a 5-10 day horizon — delivered through the SAMUDRA app and
    bulletins rather than a programmatic API, which is why spike (b) found nothing on
    ERDDAP. Ingesting it means mirroring bulletins (Phase 1.7).
    """

    required_env = ()
    integration_status = "not implemented (bulletin mirror, Phase 1.7)"

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="incois_osf",
            name="INCOIS Ocean State Forecast (INDOFOS)",
            authority_rank=1,
            variables=frozenset(
                {
                    MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
                    MarineVariable.SWELL_HEIGHT,
                    MarineVariable.CURRENT_SPEED,
                    MarineVariable.SEA_SURFACE_TEMPERATURE,
                    MarineVariable.MIXED_LAYER_DEPTH,
                    MarineVariable.DEPTH_OF_20C_ISOTHERM,
                }
            ),
            coverage=INDIAN_OCEAN,
            cadence=Cadence(period=timedelta(hours=12), grace=timedelta(hours=6)),
            license="INCOIS forecast terms",
            requires_auth=False,
            attribution="INCOIS Ocean State Forecast",
        )

    async def fetch(self, request: FetchRequest) -> FetchResult:
        raise SourceUnavailableError(
            "incois_osf",
            "OSF is published as bulletins, not an API; the mirror is Phase 1.7 and is not built",
        )


class NiotOmniBuoyAdapter(CredentialedAdapter):
    """NIOT OMNI buoy network — in-situ truth for the reliability layer.

    Twelve deep-sea buoys (7 Bay of Bengal, 5 Arabian Sea) plus 4 coastal and 2 tsunami
    buoys, transmitting hourly. These observations are what forecasts get backtested
    against for the Advisory Reliability Horizon (PLAN.md Phase 10.1), which makes this
    the highest-value blocked source in the system.

    Access is through the account-gated OMNI-RAMA portal; Phase 0.4 spike (c) confirmed
    there is no open bulk API. Everything this adapter would emit is
    ``MeasurementKind.OBSERVATION`` — never a forecast — since that distinction is the
    whole basis of skill scoring.
    """

    required_env = ("NIOT_OMNI_PORTAL_TOKEN",)
    integration_status = "not implemented (portal access not granted; Phase 1.9)"

    @property
    def descriptor(self) -> SourceDescriptor:
        return SourceDescriptor(
            source_id="niot_omni",
            name="NIOT OMNI buoy network",
            authority_rank=1,
            variables=frozenset(
                {
                    MarineVariable.SIGNIFICANT_WAVE_HEIGHT,
                    MarineVariable.SEA_SURFACE_TEMPERATURE,
                    MarineVariable.WIND_SPEED,
                    MarineVariable.WIND_DIRECTION,
                    MarineVariable.SEA_SURFACE_SALINITY,
                }
            ),
            coverage=INDIAN_OCEAN,
            cadence=Cadence(period=timedelta(hours=1), grace=timedelta(hours=3)),
            license="INCOIS/NIOT open-data policy (2018) for buoys outside the EEZ",
            requires_auth=True,
            attribution="NIOT / INCOIS OMNI buoy network",
        )
