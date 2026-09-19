-- ORCA — make evidence.observations properly bitemporal (PLAN.md Phase 3.2)
--
-- WHY THIS EXISTS. Migration 0001 keyed observations on
--     (source, variable, valid_time, lat, lon, depth_key)
-- so that re-ingesting a window updated rather than duplicated. That is right for
-- re-fetching the SAME issue, but it silently destroyed history: when a forecast was
-- revised, the new issue overwrote the old row and the original was gone.
--
-- That makes `as_of` querying impossible. A replay of yesterday's decision would see
-- today's revised forecast, so every replay would look correct and the trust layer would
-- be worthless. Deterministic replay (Phase 6.4) depends on the superseded issue still
-- being there.
--
-- Adding issued_time to the key keeps the original intent - re-fetching identical data
-- still updates in place, because the issue time is identical - while letting successive
-- issues of the same forecast coexist.

DROP INDEX IF EXISTS evidence.observations_natural_key;

CREATE UNIQUE INDEX IF NOT EXISTS observations_natural_key
    ON evidence.observations (source, variable, valid_time, issued_time, lat, lon, depth_key);

-- Serves the as_of lookup: "latest issue at or before T" for a key.
CREATE INDEX IF NOT EXISTS observations_as_of_idx
    ON evidence.observations (variable, valid_time, issued_time DESC);

COMMENT ON INDEX evidence.observations_natural_key IS
    'Bitemporal natural key. issued_time is part of the key so a revised forecast is a '
    'new row, not an overwrite: as_of queries and deterministic replay need the '
    'superseded issue to survive.';
