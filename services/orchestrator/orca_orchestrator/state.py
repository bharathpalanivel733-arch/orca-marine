"""Multi-turn conversation state and entity carry-over (PLAN.md Phase 5.6-5.7, gap G2).

The behaviour this exists for: a fisherman asks *"is it safe to go tomorrow morning off
Rameswaram?"*, gets an answer, and then asks *"and the day after?"*. The second question
carries no location, no vessel and no objective — and it must still work, because that is
how people actually speak. Losing the thread and asking "which location?" is the failure
gap G2 names.

Carry-over is **explicit and inspectable**, never implicit. Every slot records whether it
came from this turn or a previous one, so the response can say "for Rameswaram, as
before" and a reviewer can see exactly what was assumed. Silent inheritance would be
worse than asking: a wrong carried-over location produces a confident answer about the
wrong stretch of sea.

A slot is only ever carried when the new turn does not mention it. A new turn always wins.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from orca_schemas import BoundingBox, OrcaModel, TimeWindow
from pydantic import Field, field_validator, model_validator


class SlotOrigin(StrEnum):
    """Where a slot's value came from, for explanation and audit."""

    THIS_TURN = "this_turn"
    CARRIED_OVER = "carried_over"
    PROFILE_DEFAULT = "profile_default"


class Intent(StrEnum):
    """What the user is asking for. Drives which nodes the planner activates."""

    SAFETY = "safety"
    FISHING_ZONE = "fishing_zone"
    ROUTE = "route"
    CAUSAL = "causal"
    GEOFENCE = "geofence"
    UNKNOWN = "unknown"


class Slot[T](OrcaModel):
    """One resolved entity, with its provenance."""

    value: T
    origin: SlotOrigin
    turn_index: int = Field(ge=0, description="Turn that originally supplied the value.")

    @property
    def carried(self) -> bool:
        return self.origin is not SlotOrigin.THIS_TURN


class ResolvedQuery(OrcaModel):
    """A fully resolved request, after carry-over and defaults.

    ``missing_required`` is what makes clarifying questions possible: the planner asks
    only when a slot the intent genuinely needs could not be resolved from any source.
    """

    intent: Intent
    location: Slot[BoundingBox] | None = None
    place_name: Slot[str] | None = None
    time_window: Slot[TimeWindow] | None = None
    vessel_id: Slot[str] | None = None
    objective: Slot[str] | None = None
    language: Slot[str] | None = None
    missing_required: tuple[str, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return bool(self.missing_required)

    @property
    def carried_slots(self) -> tuple[str, ...]:
        """Which slots came from an earlier turn, for the 'as before' phrasing."""
        carried = []
        for name in ("location", "place_name", "time_window", "vessel_id", "objective", "language"):
            slot = getattr(self, name)
            if slot is not None and slot.carried:
                carried.append(name)
        return tuple(carried)


REQUIRED_SLOTS: dict[Intent, tuple[str, ...]] = {
    Intent.SAFETY: ("location", "time_window", "vessel_id"),
    Intent.FISHING_ZONE: ("location", "vessel_id"),
    Intent.ROUTE: ("location", "vessel_id"),
    Intent.GEOFENCE: ("location",),
    Intent.CAUSAL: ("location",),
    Intent.UNKNOWN: (),
}


class TurnRequest(OrcaModel):
    """What one turn supplied, before carry-over.

    Populated by the extractor (a strict-schema LLM call) or directly in tests. Fields are
    ``None`` when the utterance did not mention them — which is the normal case for a
    follow-up like "and tomorrow?".
    """

    utterance: str
    intent: Intent | None = None
    location: BoundingBox | None = None
    place_name: str | None = None
    time_window: TimeWindow | None = None
    vessel_id: str | None = None
    objective: str | None = None
    language: str | None = None


class SessionMemory(OrcaModel):
    """What ORCA remembers about a user between turns (PLAN.md Phase 5.7)."""

    user_id: str
    default_vessel_id: str | None = None
    home_port: str | None = None
    home_port_location: BoundingBox | None = None
    language: str = "en"
    alert_subscriptions: frozenset[str] = frozenset()


class ConversationState(OrcaModel):
    """The typed state the orchestration graph carries across turns.

    Immutable: each turn produces a new state rather than mutating one, so a transcript
    can be replayed turn by turn and the state at any point reconstructed exactly.
    """

    session_id: str
    memory: SessionMemory
    turn_index: int = Field(default=0, ge=0)
    resolved: ResolvedQuery | None = None
    history: tuple[ResolvedQuery, ...] = ()
    last_updated: datetime | None = None

    _aware = field_validator("last_updated")(
        lambda v: v if v is None or v.tzinfo is not None else None
    )

    @model_validator(mode="after")
    def _history_is_ordered(self) -> Self:
        if len(self.history) > self.turn_index + 1:
            msg = "history cannot contain more turns than have occurred"
            raise ValueError(msg)
        return self

    @property
    def previous(self) -> ResolvedQuery | None:
        """The most recent fully resolved query, if any."""
        return self.history[-1] if self.history else None

    def resolve(self, request: TurnRequest, *, now: datetime) -> ConversationState:
        """Resolve a new turn against carried context and memory.

        Precedence, highest first: **this turn**, then the previous turn, then the user's
        stored profile. A new turn always wins — carry-over fills gaps, it never
        overrides something the user just said.
        """
        previous = self.previous
        turn = self.turn_index + 1 if self.history else 0

        def pick[T](
            current: T | None, prior_slot: Slot[T] | None, profile: T | None = None
        ) -> Slot[T] | None:
            if current is not None:
                return Slot(value=current, origin=SlotOrigin.THIS_TURN, turn_index=turn)
            if prior_slot is not None:
                return Slot(
                    value=prior_slot.value,
                    origin=SlotOrigin.CARRIED_OVER,
                    turn_index=prior_slot.turn_index,
                )
            if profile is not None:
                return Slot(value=profile, origin=SlotOrigin.PROFILE_DEFAULT, turn_index=turn)
            return None

        intent = request.intent or (previous.intent if previous else Intent.UNKNOWN)

        location = pick(
            request.location,
            previous.location if previous else None,
            self.memory.home_port_location,
        )
        place_name = pick(
            request.place_name,
            previous.place_name if previous else None,
            self.memory.home_port,
        )
        # A new turn's time window is never carried: "and tomorrow?" changes the time and
        # keeps everything else, so a stale window would quietly answer the wrong day.
        time_window = (
            Slot(value=request.time_window, origin=SlotOrigin.THIS_TURN, turn_index=turn)
            if request.time_window is not None
            else None
        )
        vessel_id = pick(
            request.vessel_id,
            previous.vessel_id if previous else None,
            self.memory.default_vessel_id,
        )
        objective = pick(request.objective, previous.objective if previous else None)
        language = pick(
            request.language, previous.language if previous else None, self.memory.language
        )

        resolved = ResolvedQuery(
            intent=intent,
            location=location,
            place_name=place_name,
            time_window=time_window,
            vessel_id=vessel_id,
            objective=objective,
            language=language,
        )
        missing = tuple(name for name in REQUIRED_SLOTS[intent] if getattr(resolved, name) is None)
        resolved = resolved.model_copy(update={"missing_required": missing})

        return self.model_copy(
            update={
                "turn_index": turn,
                "resolved": resolved,
                "history": (*self.history, resolved),
                "last_updated": now,
            }
        )

    def with_default_window(self, *, now: datetime, hours: int = 24) -> ConversationState:
        """Fill an absent time window with the next ``hours``.

        Used only where an intent can proceed without an explicit time; safety cannot,
        which is why ``time_window`` is a required slot for it.
        """
        if self.resolved is None or self.resolved.time_window is not None:
            return self
        window = TimeWindow(start=now, end=now + timedelta(hours=hours))
        resolved = self.resolved.model_copy(
            update={
                "time_window": Slot(
                    value=window, origin=SlotOrigin.PROFILE_DEFAULT, turn_index=self.turn_index
                ),
                "missing_required": tuple(
                    s for s in self.resolved.missing_required if s != "time_window"
                ),
            }
        )
        return self.model_copy(
            update={"resolved": resolved, "history": (*self.history[:-1], resolved)}
        )
