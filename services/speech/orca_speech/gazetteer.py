"""Place-name resolution for ASR transcripts (PLAN.md Phase 7.4).

The single largest real-world failure mode for Indic ASR on this domain is the place name.
A fisherman says "Rameswaram"; the recognizer returns "ramesvaram", "ராமேஸ்வரம்",
"rameshwaram" or "ram eshwaram". Every one of those must resolve to the same point, because
the point is what every kernel downstream is computed at. Getting it wrong does not produce
an error — it produces a confident, correct-sounding answer about a different stretch of
sea, which is worse.

Resolution is deterministic and staged:

1. **Exact alias match** on a normalized form (case folded, diacritics stripped, spaces and
   hyphens removed). This catches the spelling variants, which are the common case.
2. **Bounded fuzzy match** by edit distance, with the allowance scaled to name length and
   capped. Long names tolerate more drift than short ones, because a two-character error in
   "Thoothukudi" is still obviously Thoothukudi while in "Okha" it is a different word.
3. **Ambiguity is returned, never resolved.** Two candidates within the threshold produce a
   clarifying question, not a guess. Asking "did you mean Kochi or Kollam?" costs a second;
   guessing wrong costs a fishing trip in the wrong direction.

**No model is involved.** An LLM asked to geocode will produce plausible coordinates for a
village it has never seen, and they will look exactly like the correct ones.

**Coordinate provenance, stated honestly.** These are approximate harbour-mouth positions
at two-decimal precision (roughly ±1 km), sufficient for selecting a forecast grid cell and
a starting point for routing. They have **not** been reconciled against an authoritative
register such as the Department of Fisheries harbour list or OSM, and
:data:`COORDINATE_PRECISION_NOTE` says so to any caller. Phase 2's geofencing uses surveyed
treaty geometry, not this table — nothing here feeds a boundary verdict.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

COORDINATE_PRECISION_NOTE = (
    "Approximate harbour positions at 2 decimal places (~1 km). Suitable for forecast grid "
    "selection; not reconciled against an authoritative harbour register, and never used "
    "for boundary determination."
)

# Edit distance allowed per name length. A short name gets no slack.
FUZZY_ALLOWANCE = ((5, 0), (8, 1), (12, 2))
MAX_FUZZY_DISTANCE = 2


class PlaceKind(StrEnum):
    """What sort of place this is. Affects nothing numeric; it disambiguates for the user."""

    FISHING_HARBOUR = "fishing_harbour"
    LANDING_CENTRE = "landing_centre"
    PORT = "port"
    COASTAL_TOWN = "coastal_town"


@dataclass(frozen=True)
class Place:
    """A named coastal location."""

    place_id: str
    name: str
    lat: float
    lon: float
    state: str
    kind: PlaceKind
    aliases: tuple[str, ...] = ()

    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


def _place(
    place_id: str,
    name: str,
    lat: float,
    lon: float,
    state: str,
    kind: PlaceKind,
    *aliases: str,
) -> Place:
    return Place(place_id, name, lat, lon, state, kind, aliases)


# A starter gazetteer covering the coastline the demo walks through, plus the major
# harbours of each maritime state. Deliberately small and hand-checked rather than a bulk
# import of unverified data: a gazetteer is only useful if its entries are trusted, and
# 1,223 PFZ coastal nodes arrive properly in Phase 1.6, from INCOIS, with their own
# provenance.
PLACES: tuple[Place, ...] = (
    # Tamil Nadu — the rehearsed demo coastline.
    _place("in-tn-chennai", "Chennai", 13.08, 80.29, "Tamil Nadu", PlaceKind.PORT,
           "சென்னை", "Madras", "Chennai Port"),
    _place("in-tn-kasimedu", "Kasimedu", 13.13, 80.30, "Tamil Nadu", PlaceKind.FISHING_HARBOUR,
           "காசிமேடு", "Chennai Fishing Harbour", "Royapuram"),
    _place("in-tn-mahabalipuram", "Mahabalipuram", 12.62, 80.19, "Tamil Nadu",
           PlaceKind.LANDING_CENTRE, "மாமல்லபுரம்", "Mamallapuram"),
    _place("in-tn-cuddalore", "Cuddalore", 11.71, 79.77, "Tamil Nadu", PlaceKind.FISHING_HARBOUR,
           "கடலூர்", "Kadalur"),
    _place("in-tn-nagapattinam", "Nagapattinam", 10.76, 79.84, "Tamil Nadu",
           PlaceKind.FISHING_HARBOUR, "நாகப்பட்டினம்", "Nagappattinam", "Nagai"),
    _place("in-tn-rameswaram", "Rameswaram", 9.29, 79.31, "Tamil Nadu", PlaceKind.FISHING_HARBOUR,
           "ராமேஸ்வரம்", "Rameshwaram", "Ramesvaram", "Ramnad"),
    _place("in-tn-pamban", "Pamban", 9.28, 79.20, "Tamil Nadu", PlaceKind.LANDING_CENTRE,
           "பாம்பன்"),
    _place("in-tn-thoothukudi", "Thoothukudi", 8.76, 78.18, "Tamil Nadu", PlaceKind.PORT,
           "தூத்துக்குடி", "Tuticorin", "Tuticorn"),
    _place("in-tn-kanyakumari", "Kanyakumari", 8.08, 77.55, "Tamil Nadu", PlaceKind.LANDING_CENTRE,
           "கன்னியாகுமரி", "Cape Comorin", "Kanniyakumari"),
    _place("in-tn-colachel", "Colachel", 8.18, 77.25, "Tamil Nadu", PlaceKind.FISHING_HARBOUR,
           "கொளச்சல்", "Kolachel"),
    # Kerala.
    _place("in-kl-kochi", "Kochi", 9.97, 76.26, "Kerala", PlaceKind.PORT,
           "കൊച്চി", "Cochin", "Ernakulam"),
    _place("in-kl-munambam", "Munambam", 10.18, 76.17, "Kerala", PlaceKind.FISHING_HARBOUR,
           "മുനമ്പം"),
    _place("in-kl-kollam", "Kollam", 8.88, 76.58, "Kerala", PlaceKind.FISHING_HARBOUR,
           "കൊല്ലം", "Quilon", "Neendakara"),
    _place("in-kl-vizhinjam", "Vizhinjam", 8.38, 76.99, "Kerala", PlaceKind.FISHING_HARBOUR,
           "വിഴിഞ്ഞം"),
    _place("in-kl-beypore", "Beypore", 11.17, 75.81, "Kerala", PlaceKind.FISHING_HARBOUR,
           "ബേപ്പൂർ", "Beypur"),
    # Andhra Pradesh and Odisha.
    _place("in-ap-visakhapatnam", "Visakhapatnam", 17.69, 83.30, "Andhra Pradesh", PlaceKind.PORT,
           "విశాఖపట్నం", "Vizag", "Vishakhapatnam"),
    _place("in-ap-kakinada", "Kakinada", 16.94, 82.25, "Andhra Pradesh", PlaceKind.FISHING_HARBOUR,
           "కాకినాడ"),
    _place("in-ap-nizampatnam", "Nizampatnam", 15.90, 80.67, "Andhra Pradesh",
           PlaceKind.FISHING_HARBOUR, "నిజాంపట్నం"),
    _place("in-od-paradip", "Paradip", 20.26, 86.67, "Odisha", PlaceKind.PORT,
           "ପାରାଦୀପ", "Paradeep", "Paradwip"),
    _place("in-od-gopalpur", "Gopalpur", 19.27, 84.91, "Odisha", PlaceKind.FISHING_HARBOUR,
           "ଗୋପାଳପୁର"),
    # West coast.
    _place("in-mh-mumbai", "Mumbai", 18.94, 72.84, "Maharashtra", PlaceKind.PORT,
           "मुंबई", "Bombay"),
    _place("in-mh-sassoon-dock", "Sassoon Dock", 18.92, 72.82, "Maharashtra",
           PlaceKind.FISHING_HARBOUR, "ससून डॉक"),
    _place("in-mh-ratnagiri", "Ratnagiri", 16.98, 73.30, "Maharashtra", PlaceKind.FISHING_HARBOUR,
           "रत्नागिरी"),
    _place("in-gj-veraval", "Veraval", 20.90, 70.37, "Gujarat", PlaceKind.FISHING_HARBOUR,
           "વેરાવળ"),
    _place("in-gj-porbandar", "Porbandar", 21.63, 69.61, "Gujarat", PlaceKind.FISHING_HARBOUR,
           "પોરબંદર"),
    _place("in-gj-okha", "Okha", 22.47, 69.07, "Gujarat", PlaceKind.PORT, "ઓખા"),
    # Islands.
    _place("in-an-port-blair", "Port Blair", 11.67, 92.75, "Andaman & Nicobar", PlaceKind.PORT,
           "पोर्ट ब्लेयर"),
)


class ResolutionKind(StrEnum):
    """How a name was resolved, or why it was not."""

    EXACT = "exact"
    FUZZY = "fuzzy"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlaceResolution:
    """The outcome of resolving one spoken place name.

    ``place`` is ``None`` for ambiguous and unknown outcomes. Callers must handle that
    rather than reaching for ``candidates[0]`` — picking the first of two near-identical
    matches is exactly the silent error this module exists to prevent.
    """

    query: str
    kind: ResolutionKind
    place: Place | None = None
    candidates: tuple[Place, ...] = ()
    distance: int = 0

    @property
    def resolved(self) -> bool:
        return self.place is not None

    @property
    def needs_clarification(self) -> bool:
        return self.kind is ResolutionKind.AMBIGUOUS


def normalize_name(name: str) -> str:
    """Fold a place name to its comparison form.

    Diacritics, case, spaces, hyphens and apostrophes all vary freely across ASR outputs
    and transliterations without changing which harbour is meant, so none of them survive.
    Indic script characters are preserved — they carry the identity of a native-script
    alias.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    kept = [
        c
        for c in decomposed
        if not unicodedata.combining(c) and (c.isalnum() or c.isspace())
    ]
    return "".join(kept).replace(" ", "").casefold()


def _build_index(places: Iterable[Place]) -> Mapping[str, tuple[Place, ...]]:
    index: dict[str, list[Place]] = {}
    for place in places:
        for name in place.all_names():
            index.setdefault(normalize_name(name), []).append(place)
    return {key: tuple(value) for key, value in index.items()}


ALIAS_INDEX: Mapping[str, tuple[Place, ...]] = _build_index(PLACES)


def _allowance(length: int) -> int:
    for threshold, allowed in FUZZY_ALLOWANCE:
        if length <= threshold:
            return allowed
    return MAX_FUZZY_DISTANCE


def edit_distance(left: str, right: str, *, limit: int) -> int:
    """Levenshtein distance, abandoned once it exceeds ``limit``.

    The bound is not only an optimisation: a distance beyond the allowance is not a worse
    match, it is not a match, so computing the true value would be wasted work.
    """
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for i, lc in enumerate(left, start=1):
        current = [i]
        for j, rc in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (lc != rc),
                )
            )
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def resolve_place(
    query: str,
    *,
    places: Sequence[Place] = PLACES,
    index: Mapping[str, tuple[Place, ...]] = ALIAS_INDEX,
) -> PlaceResolution:
    """Resolve one spoken place name to a location, or report why it could not be."""
    normalized = normalize_name(query)
    if not normalized:
        return PlaceResolution(query=query, kind=ResolutionKind.UNKNOWN)

    exact = index.get(normalized)
    if exact:
        unique = {p.place_id: p for p in exact}
        if len(unique) == 1:
            return PlaceResolution(
                query=query, kind=ResolutionKind.EXACT, place=exact[0], candidates=exact
            )
        return PlaceResolution(
            query=query,
            kind=ResolutionKind.AMBIGUOUS,
            candidates=tuple(unique.values()),
        )

    allowance = _allowance(len(normalized))
    if allowance == 0:
        return PlaceResolution(query=query, kind=ResolutionKind.UNKNOWN)

    best: dict[str, tuple[Place, int]] = {}
    for place in places:
        for name in place.all_names():
            distance = edit_distance(normalized, normalize_name(name), limit=allowance)
            if distance <= allowance:
                current = best.get(place.place_id)
                if current is None or distance < current[1]:
                    best[place.place_id] = (place, distance)

    if not best:
        return PlaceResolution(query=query, kind=ResolutionKind.UNKNOWN)

    ranked = sorted(best.values(), key=lambda item: (item[1], item[0].place_id))
    closest_distance = ranked[0][1]
    tied = [place for place, distance in ranked if distance == closest_distance]
    if len(tied) > 1:
        return PlaceResolution(
            query=query,
            kind=ResolutionKind.AMBIGUOUS,
            candidates=tuple(tied),
            distance=closest_distance,
        )
    return PlaceResolution(
        query=query,
        kind=ResolutionKind.FUZZY,
        place=ranked[0][0],
        candidates=(ranked[0][0],),
        distance=closest_distance,
    )


def find_places_in(text: str, *, max_words: int = 3) -> tuple[PlaceResolution, ...]:
    """Find every place named in a transcript.

    Multi-word names ("Port Blair", "Sassoon Dock") are matched by sliding a window of up
    to ``max_words`` and preferring the longest match, so "Port Blair" does not resolve as
    "Port" plus a stray word. Only exact matches are accepted here — fuzzy matching inside
    a free-text scan produces false positives on ordinary words, and a missed place name
    prompts a clarifying question rather than a wrong location.
    """
    words = [w for w in text.replace(",", " ").replace(".", " ").split() if w]
    found: list[PlaceResolution] = []
    seen: set[str] = set()
    index = 0
    while index < len(words):
        matched = False
        for size in range(min(max_words, len(words) - index), 0, -1):
            phrase = " ".join(words[index : index + size])
            candidates = ALIAS_INDEX.get(normalize_name(phrase))
            if not candidates:
                continue
            unique = {p.place_id: p for p in candidates}
            resolution = (
                PlaceResolution(
                    query=phrase, kind=ResolutionKind.EXACT, place=candidates[0],
                    candidates=candidates,
                )
                if len(unique) == 1
                else PlaceResolution(
                    query=phrase, kind=ResolutionKind.AMBIGUOUS, candidates=tuple(unique.values())
                )
            )
            key = resolution.place.place_id if resolution.place else phrase
            if key not in seen:
                seen.add(key)
                found.append(resolution)
            index += size
            matched = True
            break
        if not matched:
            index += 1
    return tuple(found)
