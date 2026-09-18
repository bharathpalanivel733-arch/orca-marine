"""Cadence-aware caching (PLAN.md Phase 1.12).

A cache entry's lifetime is not a guessed TTL: it is the cadence the source actually
publishes on (DEPLOYMENT.md §4 — PFZ three times a week, OSF every 12 h, CMEMS waves
every 12 h). Asking INCOIS for a 3x-weekly advisory every thirty seconds is rude and
pointless; serving a two-week-old one as current is dangerous.

The rule this module exists to enforce: **a stale entry is never returned as fresh.**
``get`` returns ``None`` once an entry passes its cadence deadline, so the caller
refetches. Where a caller genuinely wants to see what is in the cache regardless — to
show a user cached data with an explicit age banner in offline mode — it must ask for it
by name via ``get_entry`` and handle ``entry.is_stale`` itself. There is no code path
that yields stale data while implying it is current.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from orca_schemas import Cadence


@dataclass(frozen=True)
class CacheEntry[T]:
    """A cached value with the cadence that governs its lifetime."""

    value: T
    stored_at: datetime
    issued_at: datetime
    cadence: Cadence

    def is_stale(self, now: datetime) -> bool:
        """Whether the underlying data has passed its cadence deadline."""
        return self.cadence.is_stale(self.issued_at, now)

    def age_seconds(self, now: datetime) -> float:
        """Age of the *data* (not of the cache write)."""
        return (now - self.issued_at).total_seconds()


class CacheBackend[T](ABC):
    """Storage behind the cadence-aware cache.

    Implemented in-memory here. A Redis backend plugs in at this interface unchanged —
    the dev stack already runs Redis — and is deliberately left for the phase that needs
    cross-process sharing rather than being built speculatively now.
    """

    @abstractmethod
    def read(self, key: str) -> CacheEntry[T] | None: ...

    @abstractmethod
    def write(self, key: str, entry: CacheEntry[T]) -> None: ...

    @abstractmethod
    def evict(self, key: str) -> None: ...


class InMemoryCacheBackend[T](CacheBackend[T]):
    """Process-local cache backend."""

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry[T]] = {}

    def read(self, key: str) -> CacheEntry[T] | None:
        return self._entries.get(key)

    def write(self, key: str, entry: CacheEntry[T]) -> None:
        self._entries[key] = entry

    def evict(self, key: str) -> None:
        self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)


class CadenceCache[T]:
    """Cache whose expiry is the source's own publication cadence."""

    def __init__(self, backend: CacheBackend[T] | None = None) -> None:
        self._backend: CacheBackend[T] = backend or InMemoryCacheBackend()
        self.hits = 0
        self.misses = 0
        self.stale_rejections = 0

    def get(self, key: str, now: datetime) -> T | None:
        """Return the cached value only while it is fresh.

        A stale entry is evicted and reported as a miss. It is never returned here; see
        ``get_entry`` for the explicit, age-aware path.
        """
        entry = self._backend.read(key)
        if entry is None:
            self.misses += 1
            return None
        if entry.is_stale(now):
            self.stale_rejections += 1
            self.misses += 1
            self._backend.evict(key)
            return None
        self.hits += 1
        return entry.value

    def get_entry(self, key: str) -> CacheEntry[T] | None:
        """Return the raw entry, fresh or not.

        Callers using this take responsibility for surfacing the age — this is the
        offline/degraded path (PLAN.md Phase 9.6), where showing cached data with a
        visible "data age" banner is correct and showing nothing is not.
        """
        return self._backend.read(key)

    def put(
        self, key: str, value: T, *, issued_at: datetime, cadence: Cadence, now: datetime
    ) -> None:
        """Cache a value against the cadence of the source that issued it."""
        self._backend.write(
            key, CacheEntry(value=value, stored_at=now, issued_at=issued_at, cadence=cadence)
        )

    def evict(self, key: str) -> None:
        self._backend.evict(key)


def cache_key(*parts: object) -> str:
    """Build a stable cache key.

    Coordinates are rounded to ~11 km (2 decimal places) so that two queries from the
    same fishing ground share a cache entry instead of each triggering an upstream call.
    """
    rendered: list[str] = []
    for part in parts:
        if isinstance(part, float):
            rendered.append(f"{part:.2f}")
        else:
            rendered.append(str(part))
    return "|".join(rendered)
