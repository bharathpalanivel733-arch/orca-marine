-- ORCA — geospatial reference data (PLAN.md Phase 2.1)
-- Idempotent: safe to re-apply.

-- Authoritative boundary geometry. One row per boundary; the IMBL is loaded from the
-- treaty transcription in services/geo/orca_geo/imbl.py, never from a computed median.
CREATE TABLE IF NOT EXISTS geo.boundaries (
    boundary_id  text PRIMARY KEY,
    name         text NOT NULL,
    parties      text[] NOT NULL,
    derivation   text NOT NULL,   -- how the geometry was obtained; audited, not assumed
    sources      jsonb NOT NULL,  -- treaty citations + file checksums
    anomalies    jsonb NOT NULL DEFAULT '[]'::jsonb,
    geom         geography(LineString, 4326) NOT NULL,
    loaded_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN geo.boundaries.derivation IS
    'Provenance of the geometry. For the India-Sri Lanka IMBL this must record that the '
    'line was transcribed from the 1974/1976 agreements, NOT computed as a median line '
    '(METHODS.md section 3: a generated median would mis-warn fishermen).';

-- Exclusive Economic Zones (marineregions v12). Reference context, not the IMBL.
CREATE TABLE IF NOT EXISTS geo.eez (
    eez_id     text PRIMARY KEY,
    sovereign  text NOT NULL,
    name       text NOT NULL,
    source     text NOT NULL,
    geom       geography(MultiPolygon, 4326) NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);

-- Marine protected areas (WDPA / Protected Planet) and other restricted zones.
CREATE TABLE IF NOT EXISTS geo.protected_areas (
    area_id    text PRIMARY KEY,
    name       text NOT NULL,
    kind       text NOT NULL,     -- marine_protected_area | restricted_zone
    severity   text NOT NULL,     -- hard | soft
    authority  text NOT NULL,
    citation   text,
    geom       geography(MultiPolygon, 4326) NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT protected_areas_severity CHECK (severity IN ('hard', 'soft'))
);

-- Seasonal fishing bans. Dates are per-state notifications and change; they are
-- reference data, never constants in code.
CREATE TABLE IF NOT EXISTS geo.seasonal_bans (
    ban_id       text PRIMARY KEY,
    name         text NOT NULL,
    jurisdiction text NOT NULL,
    authority    text NOT NULL,
    citation     text,
    severity     text NOT NULL DEFAULT 'hard',
    start_month  smallint NOT NULL,
    start_day    smallint NOT NULL,
    end_month    smallint NOT NULL,
    end_day      smallint NOT NULL,
    geom         geography(MultiPolygon, 4326),  -- NULL = applies to the whole jurisdiction
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT seasonal_bans_months CHECK (
        start_month BETWEEN 1 AND 12 AND end_month BETWEEN 1 AND 12
    ),
    CONSTRAINT seasonal_bans_days CHECK (
        start_day BETWEEN 1 AND 31 AND end_day BETWEEN 1 AND 31
    )
);

-- Coastline (OSM / Natural Earth), used for landfall checks and route costing.
CREATE TABLE IF NOT EXISTS geo.coastline (
    segment_id text PRIMARY KEY,
    source     text NOT NULL,
    geom       geography(LineString, 4326) NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);

-- Bathymetry (GEBCO) is a raster, held as file references rather than in-table pixels;
-- the route cost surface samples it from object storage.
CREATE TABLE IF NOT EXISTS geo.bathymetry_tiles (
    tile_id    text PRIMARY KEY,
    source     text NOT NULL,
    uri        text NOT NULL,
    bbox       geography(Polygon, 4326) NOT NULL,
    loaded_at  timestamptz NOT NULL DEFAULT now()
);

-- Spatial indexes: every geofence query is a distance or containment test.
CREATE INDEX IF NOT EXISTS boundaries_geom_idx ON geo.boundaries USING GIST (geom);
CREATE INDEX IF NOT EXISTS eez_geom_idx ON geo.eez USING GIST (geom);
CREATE INDEX IF NOT EXISTS protected_areas_geom_idx ON geo.protected_areas USING GIST (geom);
CREATE INDEX IF NOT EXISTS seasonal_bans_geom_idx ON geo.seasonal_bans USING GIST (geom);
CREATE INDEX IF NOT EXISTS coastline_geom_idx ON geo.coastline USING GIST (geom);
CREATE INDEX IF NOT EXISTS bathymetry_bbox_idx ON geo.bathymetry_tiles USING GIST (bbox);
