-- ORCA — knowledge corpus for RAG (PLAN.md Phase 3.3-3.4)
-- Idempotent: safe to re-apply.

-- One row per ingested document: advisory, bulletin, SOP, ban notification, MPA rule.
-- Provenance columns are NOT NULL because an advisory without an issue time or a source
-- cannot be safely retrieved: the freshness gate has nothing to reason about.
CREATE TABLE IF NOT EXISTS evidence.documents (
    document_id     text PRIMARY KEY,
    kind            text        NOT NULL,
    title           text        NOT NULL,
    source          text        NOT NULL,
    url             text        NOT NULL,
    issued_time     timestamptz NOT NULL,
    valid_until     timestamptz,           -- when the advisory stops applying
    authority_rank  smallint    NOT NULL CHECK (authority_rank >= 1),
    license         text        NOT NULL,
    language        text        NOT NULL DEFAULT 'en',
    region_codes    text[]      NOT NULL DEFAULT '{}',  -- e.g. {IN-TN, IN-PY}
    region_geom     geography(MultiPolygon, 4326),      -- NULL = no spatial restriction
    archive_uri     text,                  -- raw payload this was parsed from
    content_sha256  text        NOT NULL,  -- dedupe + replay identity
    ingested_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT documents_validity CHECK (valid_until IS NULL OR valid_until > issued_time)
);

-- Chunks are what retrieval actually returns. Each keeps its document's provenance by
-- reference, so a passage can never be surfaced detached from where it came from.
CREATE TABLE IF NOT EXISTS evidence.document_chunks (
    chunk_id     text PRIMARY KEY,
    document_id  text        NOT NULL REFERENCES evidence.documents(document_id) ON DELETE CASCADE,
    ordinal      integer     NOT NULL,
    text         text        NOT NULL,
    -- BGE-M3 produces 1024-dimensional embeddings. The dimension is fixed in the schema
    -- so a different model cannot be written here by accident.
    embedding    vector(1024),
    token_count  integer,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,

    CONSTRAINT document_chunks_unique_ordinal UNIQUE (document_id, ordinal)
);

-- Lexical half of hybrid retrieval (BM25-style ranking via ts_rank_cd).
CREATE INDEX IF NOT EXISTS document_chunks_tsv_idx
    ON evidence.document_chunks USING GIN (tsv);

-- Vector half. HNSW with cosine distance; embeddings are L2-normalised on write, so
-- cosine and inner product agree.
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
    ON evidence.document_chunks USING hnsw (embedding vector_cosine_ops);

-- The temporal gate runs on every query, so it gets its own index.
CREATE INDEX IF NOT EXISTS documents_issued_time_idx
    ON evidence.documents (issued_time DESC);

CREATE INDEX IF NOT EXISTS documents_kind_issued_idx
    ON evidence.documents (kind, issued_time DESC);

-- Regional gate: array containment for administrative codes, GIST for geometry.
CREATE INDEX IF NOT EXISTS documents_region_codes_idx
    ON evidence.documents USING GIN (region_codes);

CREATE INDEX IF NOT EXISTS documents_region_geom_idx
    ON evidence.documents USING GIST (region_geom);

COMMENT ON TABLE evidence.documents IS
    'Knowledge corpus: PFZ advisories, ocean-state forecasts, IMD warnings, NDMA SOPs, '
    'fishing-ban notifications, MPA rules, ABIS bulletins. issued_time and region are '
    'NOT optional: retrieval refuses to return a passage it cannot date or place.';

COMMENT ON COLUMN evidence.document_chunks.embedding IS
    'BGE-M3, 1024 dimensions, L2-normalised. Dimension is pinned by the column type.';
