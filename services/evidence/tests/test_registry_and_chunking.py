"""Dataset registry and corpus chunking (PLAN.md Phase 3.1, 3.3). No database needed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orca_schemas import BoundingBox, DocumentKind, MarineVariable

from orca_evidence import (
    CorpusDocument,
    DatasetRegistry,
    DeterministicEmbedder,
    chunk_document,
)
from orca_evidence.embeddings import EMBEDDING_DIMENSIONS
from orca_evidence.registry import RejectionReason

PALK_BAY = BoundingBox(min_lat=9.0, max_lat=10.0, min_lon=79.0, max_lon=80.0)
ATLANTIC = BoundingBox(min_lat=0.0, max_lat=10.0, min_lon=-40.0, max_lon=-20.0)
TODAY = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)
IN_2013 = datetime(2013, 6, 1, tzinfo=UTC)

WAVES = MarineVariable.SIGNIFICANT_WAVE_HEIGHT
SST = MarineVariable.SEA_SURFACE_TEMPERATURE


class TestRegistrySelection:
    """The planner selects from declared metadata, never from hardcoded calls."""

    def test_selects_a_dataset_that_serves_the_variable(self) -> None:
        selection = DatasetRegistry().select(variable=WAVES, bbox=PALK_BAY, at=TODAY)

        assert selection.selected
        assert all(d.serves(WAVES) for d in selection.selected)

    def test_archive_is_rejected_for_a_present_day_question(self) -> None:
        """INCOIS SST coverage ends 2014; it must not be chosen for today."""
        selection = DatasetRegistry().select(variable=SST, bbox=PALK_BAY, at=TODAY)

        rejected = {r.dataset_id: r for r in selection.rejected}
        assert "incois_tmi_3day_datasets" in rejected
        assert (
            rejected["incois_tmi_3day_datasets"].reason is RejectionReason.OUTSIDE_TEMPORAL_COVERAGE
        )
        assert "2014-12-31" in rejected["incois_tmi_3day_datasets"].detail

    def test_the_same_archive_is_selected_for_a_question_in_its_era(self) -> None:
        """The archive is authoritative — for the hindcast, which is what it is for."""
        selection = DatasetRegistry().select(variable=SST, bbox=PALK_BAY, at=IN_2013)

        assert any(d.dataset_id == "incois_tmi_3day_datasets" for d in selection.selected)

    def test_credentialed_source_is_rejected_when_credentials_are_absent(self) -> None:
        """Do not spend a planning step on a call that cannot succeed."""
        selection = DatasetRegistry().select(
            variable=WAVES, bbox=PALK_BAY, at=TODAY, available_credentials=frozenset()
        )

        rejected = {r.dataset_id: r.reason for r in selection.rejected}
        assert rejected.get("cmems_mod_glo_wav_anfc") is RejectionReason.REQUIRES_AUTH
        assert [d.dataset_id for d in selection.selected] == ["open_meteo_marine"]

    def test_authoritative_source_wins_once_its_credentials_exist(self) -> None:
        selection = DatasetRegistry().select(
            variable=WAVES, bbox=PALK_BAY, at=TODAY, available_credentials=frozenset({"cmems"})
        )

        assert selection.best is not None
        assert selection.best.dataset_id == "cmems_mod_glo_wav_anfc"
        assert selection.best.authority_rank < 4

    def test_out_of_coverage_dataset_is_rejected(self) -> None:
        """IMD is scoped to the Indian Ocean, so it cannot serve an Atlantic box.

        Queried with WIND_SPEED, which IMD does publish: asking for waves would reject it
        as variable_not_served first and never exercise the coverage gate.
        """
        selection = DatasetRegistry().select(
            variable=MarineVariable.WIND_SPEED,
            bbox=ATLANTIC,
            at=TODAY,
            available_credentials=frozenset({"imd"}),
        )

        rejected = {r.dataset_id: r.reason for r in selection.rejected}
        assert rejected.get("imd_marine_bulletins") is RejectionReason.OUTSIDE_COVERAGE

    def test_budget_rejects_costly_datasets(self) -> None:
        selection = DatasetRegistry().select(
            variable=WAVES,
            bbox=PALK_BAY,
            at=TODAY,
            max_cost=0.0,
            available_credentials=frozenset({"cmems"}),
        )

        rejected = {r.dataset_id: r.reason for r in selection.rejected}
        assert rejected.get("cmems_mod_glo_wav_anfc") is RejectionReason.OVER_BUDGET
        assert [d.dataset_id for d in selection.selected] == ["open_meteo_marine"]

    def test_every_rejection_carries_a_reason(self) -> None:
        selection = DatasetRegistry().select(variable=WAVES, bbox=PALK_BAY, at=TODAY)

        assert selection.rejected
        assert all(r.detail for r in selection.rejected)
        assert "selected:" in selection.explain()

    def test_ordering_is_deterministic(self) -> None:
        registry = DatasetRegistry()
        first = registry.select(
            variable=WAVES, bbox=PALK_BAY, at=TODAY, available_credentials=frozenset({"cmems"})
        )
        second = registry.select(
            variable=WAVES, bbox=PALK_BAY, at=TODAY, available_credentials=frozenset({"cmems"})
        )

        assert [d.dataset_id for d in first.selected] == [d.dataset_id for d in second.selected]

    def test_unknown_dataset_lookup_lists_what_is_registered(self) -> None:
        with pytest.raises(KeyError, match="open_meteo_marine"):
            DatasetRegistry().get("nope")

    def test_duplicate_registration_is_refused(self) -> None:
        registry = DatasetRegistry()
        with pytest.raises(ValueError, match="already registered"):
            registry.register(registry.get("open_meteo_marine"))

    def test_reliability_prior_is_labelled_as_a_prior_not_a_measurement(self) -> None:
        """Phase 10.1 replaces it with backtested skill; it must not be mistaken for one."""
        field = type(DatasetRegistry().get("cmems_mod_glo_wav_anfc")).model_fields[
            "reliability_prior"
        ]
        assert "PRIOR" in (field.description or "")


class TestChunking:
    def _document(self, text: str) -> CorpusDocument:
        return CorpusDocument(
            document_id="doc-1",
            kind=DocumentKind.PFZ_ADVISORY,
            title="Test advisory",
            text=text,
            source="incois",
            url="https://incois.gov.in/test",
            issued_time=TODAY,
            authority_rank=1,
            license="INCOIS advisory terms",
        )

    def test_paragraphs_become_separate_chunks(self) -> None:
        """Paragraphs above the minimum length stay separate passages.

        Realistic advisory lengths are used deliberately: paragraphs shorter than
        ``min_chars`` are merged by design, which is covered by its own test below.
        """
        first = (
            "Fishermen along the Tamil Nadu coast are advised not to venture into the sea "
            "on account of rough conditions expected over the next 24 hours."
        )
        second = (
            "Wave heights of 2.5 to 3.5 metres are likely along and off the Palk Bay and "
            "Gulf of Mannar coasts during the same period."
        )
        document = self._document(f"{first}\n\n{second}")

        chunks = chunk_document(document)

        assert len(chunks) == 2
        assert chunks[0].ordinal == 0
        assert chunks[1].ordinal == 1
        assert chunks[0].text == first
        assert chunks[1].text == second

    def test_chunk_ids_are_stable_and_namespaced_by_document(self) -> None:
        paragraph = "Advisory paragraph text repeated to exceed the minimum chunk length. "
        document = self._document(f"{paragraph * 2}\n\n{paragraph * 2}")

        chunks = chunk_document(document)

        assert chunks[0].chunk_id == "doc-1#0000"
        assert chunks[1].chunk_id == "doc-1#0001"

    def test_long_paragraphs_split_on_sentence_boundaries(self) -> None:
        sentence = "Wave heights are expected to reach two metres near the coast. "
        document = self._document(sentence * 40)

        chunks = chunk_document(document, max_chars=400)

        assert len(chunks) > 1
        assert all(len(c.text) <= 500 for c in chunks)
        # No chunk should start mid-sentence.
        assert all(c.text[0].isupper() for c in chunks)

    def test_short_fragments_are_merged_rather_than_emitted_alone(self) -> None:
        """A two-word passage retrieves badly and reads as a non-sequitur as evidence."""
        document = self._document("A full paragraph of advisory text that stands alone.\n\nOK.")

        chunks = chunk_document(document, min_chars=40)

        assert len(chunks) == 1
        assert chunks[0].text.endswith("OK.")

    def test_content_hash_is_stable(self) -> None:
        a = self._document("identical text")
        b = self._document("identical text")
        assert a.content_sha256 == b.content_sha256
        assert len(a.content_sha256) == 64

    def test_document_requires_timezone_aware_issue_time(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="timezone-aware"):
            CorpusDocument(
                document_id="d",
                kind=DocumentKind.PFZ_ADVISORY,
                title="t",
                text="x",
                source="incois",
                url="https://example.test",
                issued_time=datetime(2026, 9, 19, 6, 0),  # noqa: DTZ001
                authority_rank=1,
                license="l",
            )

    def test_validity_must_follow_issue(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="valid_until must be after"):
            CorpusDocument(
                document_id="d",
                kind=DocumentKind.PFZ_ADVISORY,
                title="t",
                text="x",
                source="incois",
                url="https://example.test",
                issued_time=TODAY,
                valid_until=TODAY - timedelta(hours=1),
                authority_rank=1,
                license="l",
            )


class TestDeterministicEmbedder:
    def test_vectors_have_the_pinned_dimension(self) -> None:
        vector = DeterministicEmbedder().embed_query("wave height")
        assert len(vector) == EMBEDDING_DIMENSIONS

    def test_embeddings_are_stable(self) -> None:
        embedder = DeterministicEmbedder()
        assert embedder.embed_query("cyclone") == embedder.embed_query("cyclone")

    def test_vectors_are_normalised(self) -> None:
        vector = DeterministicEmbedder().embed_query("high waves expected")
        magnitude = sum(component * component for component in vector) ** 0.5
        assert magnitude == pytest.approx(1.0, abs=1e-9)

    def test_it_declares_itself_non_semantic(self) -> None:
        """So production code can assert it is never serving real retrieval."""
        assert DeterministicEmbedder().is_semantic is False
