"""Shared fallback-chain machinery for ASR and TTS (PLAN.md Phase 7.2, 7.6).

Both speech paths have the same shape: an ordered list of providers, try each until one
succeeds, and record what happened. That record is the interesting part.

ORCA's standing rule is that a degraded answer must announce its degradation (Phase 1.11).
Speech is where that rule is easiest to break, because audio has no visible provenance —
a clip produced by the third-choice provider sounds exactly as authoritative as one from
the first. So every result carries the attempt log: which providers were tried, which
failed and why, and which one actually spoke.

Failures are recorded, never raised, until the chain is exhausted. A provider being down
is expected operating condition, not an exception — Bhashini quota exhaustion during a
demo is precisely the scenario this design exists for.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProviderTier(StrEnum):
    """Where a provider sits in the documented hierarchy (PLAN.md §1.3).

    Named tiers rather than bare positions, so a result can state *what kind* of source
    produced it: a cached clip and a live neural voice are both valid, but they are not
    the same claim.
    """

    PRIMARY = "primary"
    SELF_HOSTED = "self_hosted"
    CACHE = "cache"
    CLIENT = "client"


class ProviderOutcome(StrEnum):
    """Why an attempt ended as it did."""

    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    TIMEOUT = "timeout"
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass(frozen=True)
class ProviderAttempt:
    """One provider's turn at a request."""

    provider: str
    tier: ProviderTier
    outcome: ProviderOutcome
    detail: str | None = None
    latency_ms: float | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is ProviderOutcome.SUCCESS


class ProviderUnavailableError(RuntimeError):
    """A provider declined the request. Caught by the chain and recorded as an attempt."""

    def __init__(self, detail: str, outcome: ProviderOutcome = ProviderOutcome.UNAVAILABLE) -> None:
        super().__init__(detail)
        self.detail = detail
        self.outcome = outcome


class AllProvidersFailedError(RuntimeError):
    """Every provider in the chain failed.

    Carries the full attempt log, because "speech is unavailable" is not a useful thing to
    tell a user or an on-call engineer — "Bhashini returned 429, IndicWhisper is not
    installed, nothing cached for Tamil" is.
    """

    def __init__(self, attempts: tuple[ProviderAttempt, ...]) -> None:
        summary = "; ".join(
            f"{a.provider}={a.outcome.value}" + (f" ({a.detail})" if a.detail else "")
            for a in attempts
        )
        super().__init__(f"all speech providers failed: {summary}")
        self.attempts = attempts
