-- ORCA — provenance graph and replay store (PLAN.md Phase 6.4)
-- Idempotent: safe to re-apply.
--
-- This is the concrete design that replaces the "7-point integrity check" language
-- (METHODS.md section 2): an auditable chain per run, from dataset and raw-payload hash
-- through agent and formula version to output.

CREATE TABLE IF NOT EXISTS provenance.runs (
    run_id       text PRIMARY KEY,
    session_id   text,
    turn_index   integer,
    status       text NOT NULL,          -- answer | answer_with_caveats | abstain
    fingerprint  text NOT NULL,          -- hash of graph structure + content
    recorded_at  timestamptz NOT NULL,
    verdict      jsonb NOT NULL,         -- the full TrustVerdict, checks included
    CONSTRAINT runs_status CHECK (status IN ('answer', 'answer_with_caveats', 'abstain'))
);

CREATE TABLE IF NOT EXISTS provenance.nodes (
    run_id       text NOT NULL REFERENCES provenance.runs(run_id) ON DELETE CASCADE,
    node_id      text NOT NULL,
    kind         text NOT NULL,
    label        text NOT NULL,
    occurred_at  timestamptz NOT NULL,
    attributes   jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, node_id),
    CONSTRAINT nodes_kind CHECK (
        kind IN ('dataset', 'raw_payload', 'agent', 'formula', 'output')
    )
);

CREATE TABLE IF NOT EXISTS provenance.edges (
    run_id     text NOT NULL REFERENCES provenance.runs(run_id) ON DELETE CASCADE,
    source_id  text NOT NULL,
    target_id  text NOT NULL,
    relation   text NOT NULL DEFAULT 'derives',
    PRIMARY KEY (run_id, source_id, target_id, relation)
);

-- "Which runs read this payload?" is the question asked when a source is found to have
-- published bad data and every affected decision must be identified.
CREATE INDEX IF NOT EXISTS nodes_payload_sha_idx
    ON provenance.nodes ((attributes ->> 'sha256'))
    WHERE kind = 'raw_payload';

CREATE INDEX IF NOT EXISTS nodes_formula_idx
    ON provenance.nodes ((attributes ->> 'formula_id'))
    WHERE kind = 'formula';

CREATE INDEX IF NOT EXISTS runs_recorded_at_idx ON provenance.runs (recorded_at DESC);
CREATE INDEX IF NOT EXISTS runs_status_idx ON provenance.runs (status, recorded_at DESC);

COMMENT ON TABLE provenance.runs IS
    'One row per decision run. Replay reads the graph by run_id and re-derives the '
    'decision from the archived payloads the original run actually saw.';

COMMENT ON COLUMN provenance.runs.fingerprint IS
    'Hash of graph structure and content, excluding timestamps: a replay records new '
    'wall-clock times, so including them would make the fingerprint useless for proving '
    'the decision was reproduced.';
