"""Cadence-aware cache and raw-payload archive (PLAN.md Phase 1.10, 1.12)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orca_schemas import Cadence

from orca_ingest import CadenceCache, FilesystemArchive, InMemoryCacheBackend, cache_key
from orca_ingest.archive import NullArchive

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
TWELVE_HOURLY = Cadence(period=timedelta(hours=12), grace=timedelta(hours=1))
PFZ_CADENCE = Cadence(period=timedelta(hours=56), grace=timedelta(hours=12))


class TestCadenceCache:
    def test_fresh_value_is_returned(self) -> None:
        cache: CadenceCache[str] = CadenceCache()
        cache.put("k", "waves", issued_at=NOW, cadence=TWELVE_HOURLY, now=NOW)

        assert cache.get("k", NOW + timedelta(hours=6)) == "waves"
        assert cache.hits == 1

    def test_stale_value_is_never_returned_by_get(self) -> None:
        """The guarantee: `get` yields nothing once the cadence deadline passes."""
        cache: CadenceCache[str] = CadenceCache()
        cache.put("k", "waves", issued_at=NOW, cadence=TWELVE_HOURLY, now=NOW)

        assert cache.get("k", NOW + timedelta(hours=14)) is None
        assert cache.stale_rejections == 1

    def test_stale_entry_is_evicted_so_it_cannot_resurface(self) -> None:
        cache: CadenceCache[str] = CadenceCache()
        cache.put("k", "waves", issued_at=NOW, cadence=TWELVE_HOURLY, now=NOW)

        cache.get("k", NOW + timedelta(hours=14))

        assert cache.get_entry("k") is None

    def test_explicit_path_exposes_stale_data_with_its_age(self) -> None:
        """Offline mode may show old data, but only via the age-aware accessor."""
        cache: CadenceCache[str] = CadenceCache()
        cache.put("k", "waves", issued_at=NOW, cadence=TWELVE_HOURLY, now=NOW)
        later = NOW + timedelta(hours=20)

        entry = cache.get_entry("k")

        assert entry is not None
        assert entry.is_stale(later)
        assert entry.age_seconds(later) == pytest.approx(20 * 3600)

    def test_cadence_governs_lifetime_not_a_fixed_ttl(self) -> None:
        """A 3x-weekly PFZ advisory stays valid far longer than a 12-hourly forecast."""
        cache: CadenceCache[str] = CadenceCache()
        cache.put("pfz", "advisory", issued_at=NOW, cadence=PFZ_CADENCE, now=NOW)
        cache.put("osf", "forecast", issued_at=NOW, cadence=TWELVE_HOURLY, now=NOW)

        two_days_later = NOW + timedelta(days=2)

        assert cache.get("pfz", two_days_later) == "advisory"
        assert cache.get("osf", two_days_later) is None

    def test_issue_time_not_write_time_drives_expiry(self) -> None:
        """Caching data that was already 11 h old must not grant it a fresh 12 h."""
        cache: CadenceCache[str] = CadenceCache()
        issued = NOW - timedelta(hours=11)
        cache.put("k", "waves", issued_at=issued, cadence=TWELVE_HOURLY, now=NOW)

        assert cache.get("k", NOW + timedelta(hours=3)) is None

    def test_miss_on_unknown_key(self) -> None:
        cache: CadenceCache[str] = CadenceCache(InMemoryCacheBackend())
        assert cache.get("nope", NOW) is None
        assert cache.misses == 1


class TestCacheKey:
    def test_nearby_coordinates_share_a_key(self) -> None:
        """Two boats on the same fishing ground should not trigger two upstream calls."""
        assert cache_key("open_meteo", 13.101, 80.302) == cache_key("open_meteo", 13.104, 80.298)

    def test_distant_coordinates_do_not_share_a_key(self) -> None:
        assert cache_key("open_meteo", 13.10, 80.30) != cache_key("open_meteo", 13.50, 80.30)


class TestFilesystemArchive:
    def test_payload_round_trips(self, tmp_path) -> None:
        archive = FilesystemArchive(tmp_path)
        payload = b'{"hourly": {"wave_height": [0.48]}}'

        ref = archive.store(
            payload,
            source_id="open_meteo_marine",
            content_type="application/json",
            retrieved_at=NOW,
        )

        assert archive.read(ref) == payload
        assert ref.size_bytes == len(payload)
        assert ref.source_id == "open_meteo_marine"

    def test_archiving_is_content_addressed_and_idempotent(self, tmp_path) -> None:
        """Identical bytes must not be stored twice — replay re-fetches constantly."""
        archive = FilesystemArchive(tmp_path)
        payload = b"same bytes"

        first = archive.store(payload, source_id="s", content_type="text/plain", retrieved_at=NOW)
        second = archive.store(payload, source_id="s", content_type="text/plain", retrieved_at=NOW)

        assert first.sha256 == second.sha256
        assert first.uri == second.uri
        assert len(list(tmp_path.rglob("*.txt"))) == 1

    def test_different_payloads_get_different_addresses(self, tmp_path) -> None:
        archive = FilesystemArchive(tmp_path)

        a = archive.store(b"one", source_id="s", content_type="text/plain", retrieved_at=NOW)
        b = archive.store(b"two", source_id="s", content_type="text/plain", retrieved_at=NOW)

        assert a.sha256 != b.sha256

    def test_null_archive_refuses_to_pretend_it_can_replay(self) -> None:
        ref = NullArchive().store(b"x", source_id="s", content_type="text/plain", retrieved_at=NOW)
        with pytest.raises(NotImplementedError, match="requires a real archive"):
            NullArchive().read(ref)
