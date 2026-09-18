-- ORCA — observation time series (PLAN.md Phase 1.10)
-- Idempotent: safe to apply repeatedly at service startup.

CREATE TABLE IF NOT EXISTS evidence.observations (
    variable     text        NOT NULL,
    value        double precision,          -- NULL only when quality = 'missing'
    unit         text        NOT NULL,
    lat          double precision NOT NULL,
    lon          double precision NOT NULL,
    depth_m      double precision,          -- NULL = surface
    valid_time   timestamptz NOT NULL,      -- instant the value describes
    issued_time  timestamptz NOT NULL,      -- instant the source published it
    source       text        NOT NULL,
    dataset_id   text,
    quality      text        NOT NULL,
    kind         text        NOT NULL,      -- observation | analysis | forecast
    license      text        NOT NULL,
    archive_uri  text,                      -- raw payload these rows were parsed from
    ingested_at  timestamptz NOT NULL DEFAULT now(),

    -- A generated column so the natural key works for surface rows too: NULL never
    -- equals NULL in a unique index, which would let identical surface records
    -- duplicate silently on every re-ingest.
    depth_key    double precision GENERATED ALWAYS AS (COALESCE(depth_m, -1)) STORED,

    CONSTRAINT observations_value_requires_quality
        CHECK ((value IS NULL) = (quality = 'missing')),
    CONSTRAINT observations_value_finite
        CHECK (value IS NULL OR value = value)   -- rejects NaN
);

-- Hypertable on valid_time: queries are overwhelmingly "this variable, this box, this
-- window", and retention/compression policies attach to time partitions.
SELECT create_hypertable(
    'evidence.observations', 'valid_time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Natural key. Re-ingesting a window must update, never duplicate: the cadence-aware
-- cache and deterministic replay both re-fetch the same records by design.
CREATE UNIQUE INDEX IF NOT EXISTS observations_natural_key
    ON evidence.observations (source, variable, valid_time, lat, lon, depth_key);

CREATE INDEX IF NOT EXISTS observations_variable_time
    ON evidence.observations (variable, valid_time DESC);

-- Supports "how old is the freshest record for this variable", which is what the
-- staleness gate and the provenance panel ask.
CREATE INDEX IF NOT EXISTS observations_issued_time
    ON evidence.observations (source, variable, issued_time DESC);

COMMENT ON TABLE evidence.observations IS
    'Normalized marine observations and forecasts from every ingest adapter. '
    'Provenance (source, dataset_id, issued_time, license, archive_uri) travels with '
    'each row so any number can be traced to the payload it came from.';
