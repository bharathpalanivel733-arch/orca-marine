"""Hybrid retrieval with hard temporal and regional gates (PLAN.md Phase 3.4-3.5).

Two retrievers run over the same corpus and their rankings are fused with **reciprocal
rank fusion**:

    RRF(d) = Σ over retrievers of 1 / (k + rank(d))

RRF is used rather than a weighted sum of scores because BM25 relevance and cosine
similarity are not on comparable scales; fusing *ranks* sidesteps having to invent a
normalisation and keeps the result explainable — a passage's score is a function of where
each retriever placed it, which the provenance panel can show.

**The gates are not filters applied to results — they are conditions inside both
queries.** That distinction matters: a post-filter would let an expired advisory occupy a
top-k slot and then vanish, silently returning fewer and worse results. Applied in SQL,
an out-of-date or out-of-region advisory is never a candidate at all.

Two gates, both mandatory:

* **Temporal.** ``issued_time <= as_of`` (never leak future knowledge into a replay),
  ``valid_until`` respected when the document declares one, and an optional ``max_age``
  so a 2023 advisory cannot answer a 2026 question.
* **Regional.** Administrative codes (``IN-TN``) and/or geometry containment. A document
  scoped to Gujarat is not evidence about the Palk Strait.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from orca_schemas import (
    DocumentKind,
    EvidenceProvenance,
    RetrievalMode,
    RetrievedPassage,
)

from orca_evidence.corpus import format_vector
from orca_evidence.embeddings import Embedder

DEFAULT_RRF_K = 60
DEFAULT_CANDIDATES = 50
DEFAULT_LIMIT = 8

# The gate clauses are shared by both retrievers so they cannot drift apart. If the
# lexical and vector halves ever disagreed about what is admissible, the fused result
# would depend on which retriever found a document first.
_GATE_SQL = """
    d.issued_time <= %(as_of)s
    AND (d.valid_until IS NULL OR d.valid_until > %(as_of)s)
    AND (%(min_issued)s::timestamptz IS NULL OR d.issued_time >= %(min_issued)s::timestamptz)
    AND (%(kinds)s::text[] IS NULL OR d.kind = ANY(%(kinds)s::text[]))
    AND (
        %(region_codes)s::text[] IS NULL
        OR cardinality(d.region_codes) = 0
        OR d.region_codes && %(region_codes)s::text[]
    )
    AND (
        %(region_point)s::text IS NULL
        OR d.region_geom IS NULL
        OR ST_Intersects(d.region_geom, ST_GeogFromText(%(region_point)s::text))
    )
"""

LEXICAL_SQL = f"""
SELECT c.chunk_id, c.document_id, c.text, d.kind, d.title, d.source, d.url,
       d.issued_time, d.valid_until, d.authority_rank, d.license, d.region_codes,
       d.archive_uri,
       ts_rank_cd(c.tsv, websearch_to_tsquery('english', %(query)s)) AS score
FROM evidence.document_chunks c
JOIN evidence.documents d ON d.document_id = c.document_id
WHERE c.tsv @@ websearch_to_tsquery('english', %(query)s)
  AND {_GATE_SQL}
ORDER BY score DESC, c.chunk_id
LIMIT %(candidates)s
"""

VECTOR_SQL = f"""
SELECT c.chunk_id, c.document_id, c.text, d.kind, d.title, d.source, d.url,
       d.issued_time, d.valid_until, d.authority_rank, d.license, d.region_codes,
       d.archive_uri,
       1 - (c.embedding <=> %(embedding)s::vector) AS score
FROM evidence.document_chunks c
JOIN evidence.documents d ON d.document_id = c.document_id
WHERE c.embedding IS NOT NULL
  AND {_GATE_SQL}
ORDER BY c.embedding <=> %(embedding)s::vector, c.chunk_id
LIMIT %(candidates)s
"""


@dataclass(frozen=True)
class RetrievalFilters:
    """The gates applied to a query.

    ``as_of`` is required. A retrieval without a knowledge-state anchor cannot be
    replayed, and replay is the point of the trust layer.
    """

    as_of: datetime
    max_age: timedelta | None = None
    region_codes: frozenset[str] | None = None
    region_point: tuple[float, float] | None = None
    kinds: frozenset[DocumentKind] | None = None

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            msg = "as_of must be timezone-aware"
            raise ValueError(msg)
        if self.max_age is not None and self.max_age <= timedelta(0):
            msg = "max_age must be positive"
            raise ValueError(msg)

    @property
    def min_issued(self) -> datetime | None:
        """The oldest issue time admissible under ``max_age``."""
        return None if self.max_age is None else self.as_of - self.max_age

    def as_params(self) -> dict[str, Any]:
        point = None
        if self.region_point is not None:
            lat, lon = self.region_point
            point = f"SRID=4326;POINT({lon:.6f} {lat:.6f})"
        return {
            "as_of": self.as_of,
            "min_issued": self.min_issued,
            "region_codes": sorted(self.region_codes) if self.region_codes else None,
            "region_point": point,
            "kinds": sorted(k.value for k in self.kinds) if self.kinds else None,
        }


@dataclass(frozen=True)
class _Hit:
    """One row from a retriever, before fusion."""

    chunk_id: str
    row: tuple[Any, ...]
    rank: int


def reciprocal_rank_fusion(
    rankings: dict[str, list[str]], *, k: int = DEFAULT_RRF_K
) -> dict[str, float]:
    """Fuse ranked id lists into one score per id.

    ``k`` damps the influence of top ranks so a single retriever cannot dominate on its
    own confidence; 60 is the value from the original RRF paper and is kept unless there
    is evidence to change it.
    """
    if k <= 0:
        msg = "k must be positive"
        raise ValueError(msg)
    scores: dict[str, float] = {}
    for ids in rankings.values():
        for position, identifier in enumerate(ids, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1.0 / (k + position)
    return scores


class HybridRetriever:
    """Lexical + vector retrieval over the gated corpus."""

    def __init__(
        self,
        connection: Any,
        embedder: Embedder,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        candidates: int = DEFAULT_CANDIDATES,
    ) -> None:
        self._connection = connection
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._candidates = candidates

    def retrieve(
        self, query: str, filters: RetrievalFilters, *, limit: int = DEFAULT_LIMIT
    ) -> tuple[RetrievedPassage, ...]:
        """Retrieve passages for a query under the gates.

        Returns at most ``limit`` passages, each carrying full provenance.
        """
        if not query.strip():
            msg = "query must not be empty"
            raise ValueError(msg)

        params = filters.as_params()
        lexical = self._run(LEXICAL_SQL, {**params, "query": query, "candidates": self._candidates})
        vector = self._run(
            VECTOR_SQL,
            {
                **params,
                "embedding": format_vector(self._embedder.embed_query(query)),
                "candidates": self._candidates,
            },
        )

        lexical_rank = {hit.chunk_id: hit.rank for hit in lexical}
        vector_rank = {hit.chunk_id: hit.rank for hit in vector}
        rows = {hit.chunk_id: hit.row for hit in (*vector, *lexical)}

        fused = reciprocal_rank_fusion(
            {
                "lexical": [hit.chunk_id for hit in lexical],
                "vector": [hit.chunk_id for hit in vector],
            },
            k=self._rrf_k,
        )

        ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return tuple(
            self._to_passage(
                rows[chunk_id],
                score=score,
                lexical_rank=lexical_rank.get(chunk_id),
                vector_rank=vector_rank.get(chunk_id),
            )
            for chunk_id, score in ordered
        )

    def _run(self, sql: str, params: dict[str, Any]) -> list[_Hit]:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [_Hit(chunk_id=row[0], row=row, rank=index) for index, row in enumerate(rows, 1)]

    def _to_passage(
        self,
        row: tuple[Any, ...],
        *,
        score: float,
        lexical_rank: int | None,
        vector_rank: int | None,
    ) -> RetrievedPassage:
        if lexical_rank is not None and vector_rank is not None:
            mode = RetrievalMode.HYBRID
        elif lexical_rank is not None:
            mode = RetrievalMode.LEXICAL
        else:
            mode = RetrievalMode.VECTOR

        return RetrievedPassage(
            chunk_id=row[0],
            document_id=row[1],
            text=row[2],
            kind=DocumentKind(row[3]),
            title=row[4],
            provenance=EvidenceProvenance(
                source=row[5],
                url=row[6],
                issued_time=row[7],
                authority_rank=row[9],
                license=row[10],
                provenance_id=row[0],
                archive_uri=row[12],
            ),
            mode=mode,
            score=score,
            lexical_rank=lexical_rank,
            vector_rank=vector_rank,
            region_codes=frozenset(row[11] or ()),
            valid_until=row[8],
        )
