"""Hybrid retrieval, temporal and regional gating (PLAN.md Phase 3.2, 3.4, 3.5).

Runs against the real PostgreSQL + PostGIS + pgvector stack (``-m stack``).

The gates are the point of this file. A retrieval system that returns a superseded
cyclone warning, or a Gujarat ban notification to a boat in the Palk Strait, is worse
than one that returns nothing: it produces a confident, sourced, wrong answer. Each gate
is therefore tested from both sides — the admissible document is returned, and the
inadmissible one is **absent from the result set entirely**, not merely ranked lower.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from orca_schemas import DocumentKind

from orca_evidence import (
    CorpusDocument,
    CorpusIngestor,
    DeterministicEmbedder,
    HybridRetriever,
    RetrievalFilters,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://orca:orca@localhost:5432/orca")
MIGRATIONS = Path(__file__).resolve().parents[3] / "infra" / "db" / "migrations"

NOW = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)
TAMIL_NADU = "IN-TN"
GUJARAT = "IN-GJ"
PALK_BAY_POINT = (9.6, 79.4)

WAVE_TEXT = (
    "Fishermen along the Tamil Nadu coast are advised not to venture into the sea. "
    "Significant wave heights of 3.0 to 3.5 metres are expected along and off the Palk Bay "
    "and Gulf of Mannar coasts, with squally weather and rough sea conditions."
)


def make_document(document_id: str, **overrides: object) -> CorpusDocument:
    defaults: dict[str, object] = {
        "document_id": document_id,
        "kind": DocumentKind.IMD_WARNING,
        "title": "Fishermen warning",
        "text": WAVE_TEXT,
        "source": "imd",
        "url": f"https://api.imd.gov.in/bulletin/{document_id}",
        "issued_time": NOW - timedelta(hours=3),
        "authority_rank": 1,
        "license": "IMD terms",
        "region_codes": frozenset({TAMIL_NADU}),
    }
    return CorpusDocument(**(defaults | overrides))  # type: ignore[arg-type]


@pytest.fixture
def pipeline():
    """A clean corpus plus an ingestor and retriever sharing one embedder."""
    psycopg = pytest.importorskip("psycopg")
    try:
        connection = psycopg.connect(DATABASE_URL, connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.skip(f"dev database not reachable ({exc}); run `pnpm db:up`")

    with connection.cursor() as cursor:
        for migration in ("0002_geo.sql", "0003_corpus.sql"):
            cursor.execute((MIGRATIONS / migration).read_text(encoding="utf-8"))
        cursor.execute("DELETE FROM evidence.documents WHERE document_id LIKE 'test-%'")
    connection.commit()

    embedder = DeterministicEmbedder()
    yield (
        CorpusIngestor(connection, embedder),
        HybridRetriever(connection, embedder),
        connection,
    )

    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM evidence.documents WHERE document_id LIKE 'test-%'")
    connection.commit()
    connection.close()


@pytest.mark.stack
class TestTemporalFiltering:
    """A 2023 advisory must not answer a 2026 question."""

    def test_current_advisory_is_retrieved(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-current"))

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(as_of=NOW, region_codes=frozenset({TAMIL_NADU})),
        )

        assert [r.document_id for r in results] == ["test-current"]

    def test_advisory_older_than_max_age_is_not_a_candidate(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-2023", issued_time=datetime(2023, 6, 1, tzinfo=UTC)))

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(as_of=NOW, max_age=timedelta(days=2)),
        )

        assert results == ()

    def test_expired_advisory_is_not_a_candidate(self, pipeline) -> None:
        """A warning that says when it stops applying must be honoured."""
        ingestor, retriever, _ = pipeline
        ingestor.ingest(
            make_document(
                "test-expired",
                issued_time=NOW - timedelta(days=2),
                valid_until=NOW - timedelta(hours=6),
            )
        )

        results = retriever.retrieve("wave height Palk Bay", RetrievalFilters(as_of=NOW))

        assert results == ()

    def test_advisory_still_inside_its_validity_window_is_returned(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-valid", valid_until=NOW + timedelta(hours=12)))

        results = retriever.retrieve("wave height Palk Bay", RetrievalFilters(as_of=NOW))

        assert [r.document_id for r in results] == ["test-valid"]

    def test_future_knowledge_cannot_leak_into_a_replay(self, pipeline) -> None:
        """Replaying yesterday's decision must not see an advisory issued today."""
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-later", issued_time=NOW))

        as_of_yesterday = NOW - timedelta(days=1)
        results = retriever.retrieve(
            "wave height Palk Bay", RetrievalFilters(as_of=as_of_yesterday)
        )

        assert results == ()

    def test_max_age_boundary_is_inclusive(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-edge", issued_time=NOW - timedelta(hours=24)))

        inside = retriever.retrieve(
            "wave height Palk Bay", RetrievalFilters(as_of=NOW, max_age=timedelta(hours=24))
        )
        outside = retriever.retrieve(
            "wave height Palk Bay", RetrievalFilters(as_of=NOW, max_age=timedelta(hours=23))
        )

        assert [r.document_id for r in inside] == ["test-edge"]
        assert outside == ()


@pytest.mark.stack
class TestRegionalFiltering:
    """A Gujarat notification is not evidence about the Palk Strait."""

    def test_document_from_another_state_is_not_a_candidate(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(
            make_document(
                "test-gujarat",
                kind=DocumentKind.FISHING_BAN_NOTIFICATION,
                region_codes=frozenset({GUJARAT}),
            )
        )

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(as_of=NOW, region_codes=frozenset({TAMIL_NADU})),
        )

        assert results == ()

    def test_matching_region_code_is_retrieved(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-tn", region_codes=frozenset({TAMIL_NADU})))

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(as_of=NOW, region_codes=frozenset({TAMIL_NADU})),
        )

        assert [r.document_id for r in results] == ["test-tn"]

    def test_nationally_scoped_document_is_always_admissible(self, pipeline) -> None:
        """An NDMA SOP carries no region codes and applies everywhere."""
        ingestor, retriever, _ = pipeline
        ingestor.ingest(
            make_document(
                "test-national",
                kind=DocumentKind.NDMA_SOP,
                region_codes=frozenset(),
            )
        )

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(as_of=NOW, region_codes=frozenset({TAMIL_NADU})),
        )

        assert [r.document_id for r in results] == ["test-national"]

    def test_geometry_gate_excludes_a_distant_polygon(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(
            make_document(
                "test-far-polygon",
                region_codes=frozenset(),
                region_geom_wkt="POLYGON((68 20, 70 20, 70 22, 68 22, 68 20))",
            )
        )

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(as_of=NOW, region_point=PALK_BAY_POINT),
        )

        assert results == ()

    def test_geometry_gate_includes_a_containing_polygon(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(
            make_document(
                "test-near-polygon",
                region_codes=frozenset(),
                region_geom_wkt="POLYGON((79 9, 80 9, 80 10, 79 10, 79 9))",
            )
        )

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(as_of=NOW, region_point=PALK_BAY_POINT),
        )

        assert [r.document_id for r in results] == ["test-near-polygon"]

    def test_both_gates_apply_together(self, pipeline) -> None:
        """Right region, wrong era: still inadmissible."""
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-tn-old", issued_time=datetime(2023, 1, 1, tzinfo=UTC)))
        ingestor.ingest(make_document("test-tn-now"))

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(
                as_of=NOW, region_codes=frozenset({TAMIL_NADU}), max_age=timedelta(days=1)
            ),
        )

        assert [r.document_id for r in results] == ["test-tn-now"]


@pytest.mark.stack
class TestHybridRetrievalAndProvenance:
    def test_kind_filter_narrows_the_corpus(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-warning", kind=DocumentKind.IMD_WARNING))
        ingestor.ingest(make_document("test-sop", kind=DocumentKind.NDMA_SOP))

        results = retriever.retrieve(
            "wave height Palk Bay",
            RetrievalFilters(as_of=NOW, kinds=frozenset({DocumentKind.NDMA_SOP})),
        )

        assert [r.document_id for r in results] == ["test-sop"]

    def test_every_passage_carries_full_provenance(self, pipeline) -> None:
        """PLAN.md 3.5: source, url, issued_time, age, authority rank, license, ids."""
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-prov"))

        results = retriever.retrieve("wave height Palk Bay", RetrievalFilters(as_of=NOW))

        passage = results[0]
        provenance = passage.provenance
        assert provenance.source == "imd"
        assert provenance.url.startswith("https://api.imd.gov.in/")
        assert provenance.issued_time == NOW - timedelta(hours=3)
        assert provenance.authority_rank == 1
        assert provenance.license == "IMD terms"
        assert provenance.provenance_id == passage.chunk_id
        assert passage.age_hours(NOW) == pytest.approx(3.0, abs=0.01)

    def test_a_passage_found_by_both_retrievers_is_marked_hybrid(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        ingestor.ingest(make_document("test-hybrid"))

        results = retriever.retrieve("fishermen advised rough sea", RetrievalFilters(as_of=NOW))

        assert results
        assert results[0].lexical_rank is not None
        assert results[0].vector_rank is not None
        assert results[0].mode.value == "hybrid"

    def test_results_are_ordered_by_fused_score(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        for index in range(3):
            ingestor.ingest(make_document(f"test-rank-{index}"))

        results = retriever.retrieve("rough sea conditions", RetrievalFilters(as_of=NOW))

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_limit_is_respected(self, pipeline) -> None:
        ingestor, retriever, _ = pipeline
        for index in range(5):
            ingestor.ingest(make_document(f"test-limit-{index}"))

        results = retriever.retrieve("rough sea conditions", RetrievalFilters(as_of=NOW), limit=2)

        assert len(results) == 2

    def test_reingestion_replaces_chunks_rather_than_duplicating(self, pipeline) -> None:
        """A revised advisory must not leave stale passages behind."""
        ingestor, retriever, connection = pipeline
        ingestor.ingest(make_document("test-revise", text=WAVE_TEXT))
        ingestor.ingest(
            make_document("test-revise", text="Conditions have improved. Fishing may resume.")
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM evidence.document_chunks WHERE document_id = 'test-revise'"
            )
            chunk_count = cursor.fetchone()[0]

        results = retriever.retrieve("fishing may resume", RetrievalFilters(as_of=NOW))

        assert chunk_count == 1
        assert "improved" in results[0].text

    def test_embedding_dimension_is_enforced_by_the_column(self, pipeline) -> None:
        """The schema pins 1024 dimensions, so a wrong-sized model cannot be stored."""
        import psycopg

        _, _, connection = pipeline
        with pytest.raises(psycopg.errors.DataException), connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO evidence.documents (document_id, kind, title, source, url, "
                "issued_time, authority_rank, license, content_sha256) VALUES "
                "('test-dim', 'ndma_sop', 't', 's', 'u', now(), 1, 'l', 'x')"
            )
            cursor.execute(
                "INSERT INTO evidence.document_chunks (chunk_id, document_id, ordinal, text, "
                "embedding) VALUES ('test-dim#0', 'test-dim', 0, 'x', %s::vector)",
                ("[0.1,0.2,0.3]",),
            )
        connection.rollback()
