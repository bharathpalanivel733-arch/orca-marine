"""Evidence-layer contracts (PLAN.md Phase 3).

Two kinds of evidence reach the reasoning layer, and both must arrive carrying the same
provenance so the trust layer can weigh them on equal terms:

* **structured** — numeric records from the ingest adapters (Phase 1), queried with
  ``as_of`` semantics so a decision can be replayed exactly as it was made;
* **unstructured** — passages retrieved from advisories, bulletins, SOPs, ban
  notifications and MPA rules.

The rule this module enforces is that **no evidence item can exist without its
provenance**. ``source``, ``url``, ``issued_time``, ``authority_rank``, ``license`` and a
provenance identifier are required fields, not optional decoration. A retrieved passage
with no issue time is not a weaker citation — it is unusable, because the freshness gate
(Phase 6.3) cannot reason about it and the provenance panel cannot display it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from pydantic import Field, field_validator, model_validator

from orca_schemas.base import OrcaModel
from orca_schemas.ingest import BoundingBox, Cadence, MarineVariable


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must be timezone-aware"
        raise ValueError(msg)
    return value


class DocumentKind(StrEnum):
    """What sort of document a passage came from.

    The kind drives default retention and authority, so it is part of the contract rather
    than a free-text tag: a statutory ban notification and a discussion note must not be
    retrievable on the same footing.
    """

    PFZ_ADVISORY = "pfz_advisory"
    OCEAN_STATE_FORECAST = "ocean_state_forecast"
    IMD_WARNING = "imd_warning"
    CYCLONE_BULLETIN = "cyclone_bulletin"
    NDMA_SOP = "ndma_sop"
    FISHING_BAN_NOTIFICATION = "fishing_ban_notification"
    MPA_RULE = "mpa_rule"
    ABIS_BULLETIN = "abis_bulletin"


class RetrievalMode(StrEnum):
    """Which retriever produced a hit, kept for explainability of the ranking."""

    LEXICAL = "lexical"
    VECTOR = "vector"
    HYBRID = "hybrid"


class DatasetCapability(OrcaModel):
    """What one tool or dataset can serve (PLAN.md Phase 3.1).

    This is the metadata the planner selects on. It exists so source choice is a lookup
    over declared capabilities rather than a hardcoded call — which is what "autonomous
    dataset discovery" (G3) actually means in practice.
    """

    dataset_id: str = Field(description="Stable id, e.g. 'cmems_wav_anfc'.")
    source_id: str = Field(description="The adapter that serves it, e.g. 'cmems'.")
    name: str
    variables: frozenset[MarineVariable]
    coverage_bbox: BoundingBox
    cadence: Cadence
    latency: timedelta = Field(
        description="Typical delay between an observation and its publication."
    )
    authority_rank: int = Field(
        ge=1, description="1 = authoritative Indian agency; higher = further down the chain."
    )
    reliability_prior: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Prior confidence before any backtesting. A PRIOR, not a measured skill score: "
            "Phase 10.1 replaces it with CRPS/Brier results against buoy observations."
        ),
    )
    cost: float = Field(
        ge=0.0, description="Relative cost of one call, for the planner's budget (0 = free)."
    )
    license: str
    requires_auth: bool = False
    temporal_coverage_start: datetime | None = None
    temporal_coverage_end: datetime | None = Field(
        default=None,
        description="Set when a dataset is an archive. None means it is still being updated.",
    )
    notes: str | None = None

    _aware = field_validator("temporal_coverage_start", "temporal_coverage_end")(
        lambda v: _require_aware(v) if v is not None else None
    )

    @property
    def is_archive(self) -> bool:
        """Whether this dataset stopped updating.

        True for every INCOIS ERDDAP dataset (Phase 1 finding): they are historical
        archives, so the planner must not choose them for a present-day question.
        """
        return self.temporal_coverage_end is not None

    def serves(self, variable: MarineVariable) -> bool:
        return variable in self.variables

    def covers_time(self, moment: datetime) -> bool:
        """Whether the dataset's coverage includes an instant."""
        moment = _require_aware(moment)
        if self.temporal_coverage_start and moment < self.temporal_coverage_start:
            return False
        return not (self.temporal_coverage_end and moment > self.temporal_coverage_end)


class EvidenceProvenance(OrcaModel):
    """Where one piece of evidence came from.

    Required on every evidence item, structured or retrieved. ``age`` is computed against
    a caller-supplied clock rather than stored, so it can never go stale in the record
    itself.
    """

    source: str = Field(description="Source or publisher id, e.g. 'incois'.")
    url: str = Field(description="Where a human can go and read the original.")
    issued_time: datetime = Field(description="When the publisher issued it.")
    authority_rank: int = Field(ge=1)
    license: str
    provenance_id: str = Field(
        description="Stable identifier for replay: document id, chunk id or record key."
    )
    retrieved_at: datetime | None = None
    archive_uri: str | None = Field(
        default=None, description="Archived raw payload, when one exists (Phase 1.10)."
    )

    _aware_issued = field_validator("issued_time")(_require_aware)

    def age(self, now: datetime) -> timedelta:
        """How old the evidence is as of ``now``."""
        return _require_aware(now) - self.issued_time

    def age_hours(self, now: datetime) -> float:
        return self.age(now).total_seconds() / 3600.0


class RetrievedPassage(OrcaModel):
    """One passage returned from the knowledge corpus (PLAN.md Phase 3.5)."""

    chunk_id: str
    document_id: str
    kind: DocumentKind
    title: str
    text: str
    provenance: EvidenceProvenance
    mode: RetrievalMode
    score: float = Field(description="Fused rank score; higher is better.")
    lexical_rank: int | None = Field(default=None, ge=1)
    vector_rank: int | None = Field(default=None, ge=1)
    region_codes: frozenset[str] = frozenset()
    valid_until: datetime | None = Field(
        default=None, description="When the advisory stops applying, if it says so."
    )

    _aware_valid = field_validator("valid_until")(
        lambda v: _require_aware(v) if v is not None else None
    )

    @model_validator(mode="after")
    def _require_a_contributing_rank(self) -> Self:
        if self.lexical_rank is None and self.vector_rank is None:
            msg = "a passage must have been ranked by at least one retriever"
            raise ValueError(msg)
        return self

    def age_hours(self, now: datetime) -> float:
        return self.provenance.age_hours(now)

    def is_expired(self, now: datetime) -> bool:
        """Whether the advisory's own validity window has passed."""
        return self.valid_until is not None and _require_aware(now) >= self.valid_until
