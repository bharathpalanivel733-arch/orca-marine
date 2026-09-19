"""Place-name resolution (PLAN.md Phase 7.4).

The failure being guarded against is not a crash. It is a confident, fluent, correct-
sounding answer about the wrong stretch of sea, produced because "Rameswaram" came back
from the recognizer as "ramesvaram" and something helpfully guessed.
"""

from __future__ import annotations

import pytest

from orca_speech import (
    PLACES,
    Place,
    ResolutionKind,
    find_places_in,
    normalize_name,
    resolve_place,
)


class TestSpellingVariants:
    @pytest.mark.parametrize(
        ("spoken", "expected_id"),
        [
            ("Rameswaram", "in-tn-rameswaram"),
            ("rameshwaram", "in-tn-rameswaram"),
            ("Ramesvaram", "in-tn-rameswaram"),
            ("ராமேஸ்வரம்", "in-tn-rameswaram"),
            ("Thoothukudi", "in-tn-thoothukudi"),
            ("Tuticorin", "in-tn-thoothukudi"),
            ("தூத்துக்குடி", "in-tn-thoothukudi"),
            ("Cochin", "in-kl-kochi"),
            ("Vizag", "in-ap-visakhapatnam"),
            ("Madras", "in-tn-chennai"),
        ],
    )
    def test_variants_resolve_to_one_place(self, spoken: str, expected_id: str) -> None:
        resolution = resolve_place(spoken)

        assert resolution.place is not None
        assert resolution.place.place_id == expected_id

    def test_case_and_spacing_do_not_matter(self) -> None:
        assert normalize_name("Port Blair") == normalize_name("port  blair")
        assert resolve_place("PORT BLAIR").place is not None

    def test_a_native_script_name_resolves_to_the_same_point_as_the_roman_one(self) -> None:
        tamil = resolve_place("நாகப்பட்டினம்")
        roman = resolve_place("Nagapattinam")

        assert tamil.place == roman.place


class TestFuzzyMatching:
    def test_a_small_transcription_error_still_resolves(self) -> None:
        resolution = resolve_place("ramesvarm")

        assert resolution.kind is ResolutionKind.FUZZY
        assert resolution.place is not None
        assert resolution.place.place_id == "in-tn-rameswaram"

    def test_a_short_name_gets_no_slack(self) -> None:
        """Two characters of drift in "Okha" is a different word, not a typo."""
        assert resolve_place("Olha").kind is ResolutionKind.UNKNOWN

    def test_an_unrelated_word_is_not_forced_onto_a_place(self) -> None:
        assert resolve_place("tomorrow").kind is ResolutionKind.UNKNOWN
        assert resolve_place("").kind is ResolutionKind.UNKNOWN

    def test_resolution_is_deterministic(self) -> None:
        results = {
            (r.kind, r.place.place_id if r.place else None)
            for r in (resolve_place("ramesvarm") for _ in range(20))
        }

        assert results == {(ResolutionKind.FUZZY, "in-tn-rameswaram")}


class TestAmbiguityIsNotResolved:
    def test_two_equally_close_candidates_produce_a_clarifying_question(self) -> None:
        """Asking costs a second; guessing costs a trip in the wrong direction."""
        near = (
            Place("a", "Kadalur", 11.7, 79.7, "TN", PLACES[0].kind),
            Place("b", "Kadalar", 12.7, 79.9, "TN", PLACES[0].kind),
        )

        resolution = resolve_place("kadalxr", places=near, index={})

        assert resolution.kind is ResolutionKind.AMBIGUOUS
        assert resolution.place is None
        assert resolution.needs_clarification
        assert len(resolution.candidates) == 2

    def test_an_ambiguous_result_exposes_no_default_place(self) -> None:
        """Callers must not be able to reach for candidates[0] and call it resolved."""
        near = (
            Place("a", "Kadalur", 11.7, 79.7, "TN", PLACES[0].kind),
            Place("b", "Kadalar", 12.7, 79.9, "TN", PLACES[0].kind),
        )

        resolution = resolve_place("kadalxr", places=near, index={})

        assert not resolution.resolved


class TestTranscriptScanning:
    def test_places_are_found_in_a_sentence(self) -> None:
        found = find_places_in("I am going from Rameswaram towards Nagapattinam tomorrow")

        assert [r.place.place_id for r in found if r.place] == [
            "in-tn-rameswaram",
            "in-tn-nagapattinam",
        ]

    def test_a_multi_word_name_is_matched_whole(self) -> None:
        """"Port Blair" must not resolve as "Port" plus a leftover word."""
        found = find_places_in("heading to Port Blair")

        assert len(found) == 1
        assert found[0].place is not None
        assert found[0].place.place_id == "in-an-port-blair"

    def test_a_sentence_with_no_place_yields_nothing(self) -> None:
        assert find_places_in("is it safe to go out today") == ()

    def test_the_same_place_named_twice_appears_once(self) -> None:
        found = find_places_in("from Kochi to Kochi")

        assert len(found) == 1


class TestGazetteerData:
    def test_place_ids_are_unique(self) -> None:
        ids = [p.place_id for p in PLACES]

        assert len(ids) == len(set(ids))

    def test_every_coordinate_is_in_the_indian_ocean_region(self) -> None:
        """A transposed lat/lon would put a harbour in the Sahara; this catches that."""
        for place in PLACES:
            assert 6.0 <= place.lat <= 24.0, place.place_id
            assert 68.0 <= place.lon <= 94.0, place.place_id

    def test_no_two_places_share_an_alias(self) -> None:
        """A shared alias would make one of them permanently unreachable by that name."""
        seen: dict[str, str] = {}
        for place in PLACES:
            for name in place.all_names():
                key = normalize_name(name)
                assert key not in seen or seen[key] == place.place_id, (
                    f"{name!r} is claimed by both {seen.get(key)} and {place.place_id}"
                )
                seen[key] = place.place_id
