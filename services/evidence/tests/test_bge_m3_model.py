"""Real BGE-M3 embedding checks (PLAN.md Phase 3.3).

Opt-in via ``-m model``: the first run downloads roughly 2 GB of weights from the model
hub, which has no place in the default suite.

What these assert is that the *production* embedder behaves as the schema and the
retrieval layer assume — 1024 dimensions, unit-normalised — and that it actually encodes
meaning, which the deterministic test embedder explicitly does not. Without this, the
only thing ever exercised would be the hash embedder, and "we use BGE-M3" would be a
claim about a code path nobody had run.
"""

from __future__ import annotations

import pytest

from orca_evidence import BgeM3Embedder
from orca_evidence.embeddings import EMBEDDING_DIMENSIONS

ADVISORY = "Fishermen are advised not to venture into the sea due to rough conditions."
UNRELATED = "Chennai suburban railway timetable for express trains."
QUERY = "Is it safe to go fishing tomorrow near Rameswaram?"


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture(scope="module")
def embedder() -> BgeM3Embedder:
    if not BgeM3Embedder.available():
        pytest.skip(
            "sentence-transformers not installed; pip install -e 'services/evidence[embeddings]'"
        )
    return BgeM3Embedder()


@pytest.mark.model
class TestBgeM3:
    def test_dimension_matches_the_pinned_column(self, embedder: BgeM3Embedder) -> None:
        """The vector(1024) column would reject anything else."""
        assert len(embedder.embed_query(QUERY)) == EMBEDDING_DIMENSIONS

    def test_vectors_are_unit_normalised(self, embedder: BgeM3Embedder) -> None:
        """Cosine and inner product must agree for the HNSW index to behave."""
        vector = embedder.embed_query(QUERY)
        magnitude = sum(component * component for component in vector) ** 0.5
        assert magnitude == pytest.approx(1.0, abs=1e-4)

    def test_it_encodes_meaning_not_just_tokens(self, embedder: BgeM3Embedder) -> None:
        """A safety query must sit closer to a marine advisory than to a train timetable.

        Note the query and the advisory share almost no vocabulary, so a lexical matcher
        would not rank them together — this is the thing the deterministic embedder
        cannot do and the reason BGE-M3 is the production choice.
        """
        query = embedder.embed_query(QUERY)
        advisory, unrelated = embedder.embed_documents([ADVISORY, UNRELATED])

        assert cosine(query, advisory) > cosine(query, unrelated)

    def test_it_declares_itself_semantic(self, embedder: BgeM3Embedder) -> None:
        assert embedder.is_semantic is True
        assert embedder.model_id == "BAAI/bge-m3"

    def test_cross_lingual_retrieval_works(self, embedder: BgeM3Embedder) -> None:
        """A Tamil query must retrieve an English advisory.

        This is why BGE-M3 was chosen over an English-only encoder: ORCA's users ask in
        Tamil and Hindi while INCOIS and IMD publish in English.
        """
        tamil_query = embedder.embed_query("நாளை மீன்பிடிக்க கடலுக்கு செல்வது பாதுகாப்பானதா?")
        advisory, unrelated = embedder.embed_documents([ADVISORY, UNRELATED])

        assert cosine(tamil_query, advisory) > cosine(tamil_query, unrelated)
