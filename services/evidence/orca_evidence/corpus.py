"""Knowledge-corpus ingestion (PLAN.md Phase 3.3).

Documents enter the corpus only with complete provenance. ``source``, ``url``,
``issued_time``, ``authority_rank`` and ``license`` are required by the model, not
merely encouraged, because a passage that cannot be dated cannot pass the freshness gate
and a passage that cannot be attributed cannot be shown in the provenance panel. It is
better to refuse to ingest such a document than to retrieve it later and be unable to
say where it came from.

Chunking is paragraph-aware with a size cap: advisories are short and structured, and
splitting mid-sentence would hand the retriever fragments that read as instructions
without their conditions ("...may proceed" detached from "if wave height is below").
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from orca_schemas import DocumentKind, OrcaModel
from pydantic import Field, field_validator, model_validator

from orca_evidence.embeddings import Embedder

DEFAULT_MAX_CHUNK_CHARS = 1200
DEFAULT_MIN_CHUNK_CHARS = 80

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = "datetime must be timezone-aware"
        raise ValueError(msg)
    return value


class CorpusDocument(OrcaModel):
    """A document to ingest, with the provenance retrieval will need."""

    document_id: str
    kind: DocumentKind
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    url: str = Field(min_length=1)
    issued_time: datetime
    valid_until: datetime | None = None
    authority_rank: int = Field(ge=1)
    license: str = Field(min_length=1)
    language: str = "en"
    region_codes: frozenset[str] = frozenset()
    region_geom_wkt: str | None = Field(
        default=None,
        description=(
            "Optional spatial extent. None means the document is not geographically scoped."
        ),
    )
    archive_uri: str | None = None

    _aware_issued = field_validator("issued_time")(_require_aware)
    _aware_valid = field_validator("valid_until")(
        lambda v: _require_aware(v) if v is not None else None
    )

    @model_validator(mode="after")
    def _validity_after_issue(self) -> CorpusDocument:
        if self.valid_until is not None and self.valid_until <= self.issued_time:
            msg = "valid_until must be after issued_time"
            raise ValueError(msg)
        return self

    @property
    def content_sha256(self) -> str:
        """Content address, used for dedupe and replay identity."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Chunk:
    """One retrievable passage."""

    chunk_id: str
    document_id: str
    ordinal: int
    text: str

    @property
    def token_estimate(self) -> int:
        """Rough token count. Deliberately an estimate, and named as one."""
        return max(1, len(self.text) // 4)


def chunk_document(
    document: CorpusDocument,
    *,
    max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    min_chars: int = DEFAULT_MIN_CHUNK_CHARS,
) -> tuple[Chunk, ...]:
    """Split a document into passages on paragraph, then sentence, boundaries.

    Short trailing fragments are merged back into the previous chunk rather than emitted
    alone: a two-word passage retrieves badly and reads as a non-sequitur when shown as
    evidence.
    """
    if max_chars <= min_chars:
        msg = "max_chars must exceed min_chars"
        raise ValueError(msg)

    pieces: list[str] = []
    for paragraph in _PARAGRAPH_RE.split(document.text.strip()):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces.append(paragraph)
            continue
        current = ""
        for sentence in _SENTENCE_RE.split(paragraph):
            if current and len(current) + len(sentence) + 1 > max_chars:
                pieces.append(current.strip())
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            pieces.append(current.strip())

    merged: list[str] = []
    for piece in pieces:
        if merged and len(piece) < min_chars:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)

    return tuple(
        Chunk(
            chunk_id=f"{document.document_id}#{ordinal:04d}",
            document_id=document.document_id,
            ordinal=ordinal,
            text=text,
        )
        for ordinal, text in enumerate(merged)
    )


UPSERT_DOCUMENT_SQL = """
INSERT INTO evidence.documents (
    document_id, kind, title, source, url, issued_time, valid_until,
    authority_rank, license, language, region_codes, region_geom, archive_uri, content_sha256
) VALUES (
    %(document_id)s, %(kind)s, %(title)s, %(source)s, %(url)s, %(issued_time)s, %(valid_until)s,
    %(authority_rank)s, %(license)s, %(language)s, %(region_codes)s,
    CASE WHEN %(region_wkt)s::text IS NULL THEN NULL
         ELSE ST_Multi(ST_GeomFromText(%(region_wkt)s::text, 4326))::geography END,
    %(archive_uri)s, %(content_sha256)s
)
ON CONFLICT (document_id) DO UPDATE SET
    kind = EXCLUDED.kind, title = EXCLUDED.title, source = EXCLUDED.source,
    url = EXCLUDED.url, issued_time = EXCLUDED.issued_time, valid_until = EXCLUDED.valid_until,
    authority_rank = EXCLUDED.authority_rank, license = EXCLUDED.license,
    language = EXCLUDED.language, region_codes = EXCLUDED.region_codes,
    region_geom = EXCLUDED.region_geom, archive_uri = EXCLUDED.archive_uri,
    content_sha256 = EXCLUDED.content_sha256, ingested_at = now()
"""

INSERT_CHUNK_SQL = """
INSERT INTO evidence.document_chunks (chunk_id, document_id, ordinal, text, embedding, token_count)
VALUES (%(chunk_id)s, %(document_id)s, %(ordinal)s, %(text)s, %(embedding)s, %(token_count)s)
ON CONFLICT (chunk_id) DO UPDATE SET
    text = EXCLUDED.text, embedding = EXCLUDED.embedding, token_count = EXCLUDED.token_count
"""


def format_vector(vector: Sequence[float]) -> str:
    """pgvector literal format."""
    return "[" + ",".join(f"{component:.8f}" for component in vector) + "]"


class CorpusIngestor:
    """Chunks, embeds and stores documents."""

    def __init__(self, connection: Any, embedder: Embedder) -> None:
        self._connection = connection
        self._embedder = embedder

    def ingest(self, document: CorpusDocument) -> tuple[Chunk, ...]:
        """Ingest one document. Idempotent on ``document_id``.

        Re-ingesting replaces the document's chunks rather than appending, so a revised
        advisory cannot leave stale passages behind to be retrieved later.
        """
        chunks = chunk_document(document)
        if not chunks:
            msg = f"document {document.document_id!r} produced no chunks"
            raise ValueError(msg)

        vectors = self._embedder.embed_documents([chunk.text for chunk in chunks])

        with self._connection.cursor() as cursor:
            cursor.execute(
                UPSERT_DOCUMENT_SQL,
                {
                    "document_id": document.document_id,
                    "kind": document.kind.value,
                    "title": document.title,
                    "source": document.source,
                    "url": document.url,
                    "issued_time": document.issued_time,
                    "valid_until": document.valid_until,
                    "authority_rank": document.authority_rank,
                    "license": document.license,
                    "language": document.language,
                    "region_codes": sorted(document.region_codes),
                    "region_wkt": document.region_geom_wkt,
                    "archive_uri": document.archive_uri,
                    "content_sha256": document.content_sha256,
                },
            )
            cursor.execute(
                "DELETE FROM evidence.document_chunks WHERE document_id = %(document_id)s",
                {"document_id": document.document_id},
            )
            for chunk, vector in zip(chunks, vectors, strict=True):
                cursor.execute(
                    INSERT_CHUNK_SQL,
                    {
                        "chunk_id": chunk.chunk_id,
                        "document_id": chunk.document_id,
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                        "embedding": format_vector(vector),
                        "token_count": chunk.token_estimate,
                    },
                )
        self._connection.commit()
        return chunks

    def ingest_many(self, documents: Sequence[CorpusDocument]) -> int:
        """Ingest several documents, returning the chunk count."""
        return sum(len(self.ingest(document)) for document in documents)
