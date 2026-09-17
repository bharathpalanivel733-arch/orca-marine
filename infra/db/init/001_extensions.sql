-- ORCA — database bootstrap (PLAN.md Phase 0.2)
-- Runs once, on first container start, against the POSTGRES_DB.

-- Structured geospatial evidence (Phase 2: EEZ / IMBL / MPA polygons, distance & containment).
CREATE EXTENSION IF NOT EXISTS postgis;
-- Per-coastal-node forecast/observation time series (Phase 1.10).
CREATE EXTENSION IF NOT EXISTS timescaledb;
-- Knowledge-corpus embeddings for RAG over advisories/bulletins/SOPs (Phase 3.3).
CREATE EXTENSION IF NOT EXISTS vector;
-- Lexical half of hybrid retrieval (Phase 3.4).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Namespaces. Tables are created by later phases; Phase 0 only establishes the layout.
CREATE SCHEMA IF NOT EXISTS evidence;    -- Phase 1/3: ingested records, corpus chunks
CREATE SCHEMA IF NOT EXISTS geo;         -- Phase 2: boundary, MPA and ban-zone geometry
CREATE SCHEMA IF NOT EXISTS provenance;  -- Phase 6: run graph, replay payload hashes

COMMENT ON SCHEMA evidence   IS 'Ingested structured evidence and embedded knowledge corpus.';
COMMENT ON SCHEMA geo        IS 'Authoritative boundary geometry (EEZ, agreed 1974/76 IMBL, MPAs, ban zones).';
COMMENT ON SCHEMA provenance IS 'Deterministic replay: dataset -> timestamp -> agent -> formula -> output.';
