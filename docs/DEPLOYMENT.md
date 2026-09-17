# DEPLOYMENT.md — Data Sources, Ingestion & Infra

## 1. Deployment stack (as pitched in the PPT)

- **Docker, Google Cloud Run** — containerized services, auto-scale with regional query load.
- Current known deployment: public site at **https://orca-official.vercel.app/** (per PPT).

## 2. Data sources — verified against live endpoints (gap-closure report)

Build each source as an **adapter behind a common cache** so any one source can fail without taking down
the platform.

### INCOIS — the primary Indian authority
- **INCOIS ERDDAP** — `https://erddap.incois.gov.in/erddap` (server v2.30.0). Open, no key. Best
  programmatic Indian source. Confirmed live dataset IDs:
  - `incois_tmi_3day_datasets` — 3-day TMI SST (griddap)
  - `NOAA_AVHRR_AMSR_datasets` — blended daily OI SST (griddap)
  - `incois_quickscat_daily_datasets`, `ascat_daily_datasets`, `ascat_mnt_datasets` — scatterometer winds
    (griddap)
  - `incois_oceansat2_datasets` — Oceansat-2 OCM ocean colour / chlorophyll (griddap)
  - `Indian_ARGO_Floats` — ARGO profiles (tabledap)
  - griddap URL pattern: `…/griddap/<id>.nc?SST[(2026-09-01T00:00:00Z)][(5):1:(25)][(70):1:(95)]`
    (Bay-of-Bengal box; verify dim order/names via `.das` / Data Access Form). Filetypes: `.nc`, `.csv`,
    `.json`, `.mat`, `.geotif`, `.transparentPng`.
  - tabledap pattern: `…/tabledap/<id>.csv?var1,var2,time,latitude,longitude&time>=…
    &latitude>=5&latitude<=25&longitude>=70&longitude<=95`
  - **Caveat:** the exact griddap ID for the INCOIS wave / Ocean-State-Forecast product was **not
    confirmable by name** — verify live in the ERDDAP catalog (`…/erddap/search/index.html`). Use CMEMS
    as the reliable wave source instead.
- **INCOIS PFZ advisories** — issued 3×/week. WebGIS at `incois.gov.in/MarineFisheries/PfzWebGis`,
  geoportal `incois.gov.in/geoportal/MFASPFZ`, text/bulletin at
  `incois.gov.in/MarineFisheries/TextDataHome`. **No clean JSON API** — must parse text advisories /
  WebGIS (WMS) layers. Cache per coastal node (~1,223 nodes published).
- **INCOIS Ocean State Forecast (INDOFOS)** — significant wave height, swell, currents, SST, mixed-layer
  depth, depth-of-20°C isotherm, astronomical tides, wind; 3-hour intervals, 5–10-day horizon. Delivered
  via SAMUDRA app / bulletins — scrape/mirror OSF bulletins or use CMEMS equivalents.
- **INCOIS ABIS** (Algal Bloom Information Service) — operational HAB (Noctiluca) detection service, a
  real anchor for the anomaly-detection module.

### IMD — weather, cyclone, marine bulletins
- `https://api.imd.gov.in/api/v1/…` — public JSON, no `data.gov.in` key required (IP-whitelisting
  optional for heavy use; IMD asks for attribution + client caching). Relevant endpoints (JSON, several
  GeoJSON):
  - `seabulletin?id=…`, `coastalbulletin`, `portwarning?id=…`, `fishermen-warning`
  - `cyclone_track` (observed + forecast positions, MSW, category), `cyclone_wind` (GeoJSON 27/34/50/64-kt
    wind radii), `cyclone_cou` (GeoJSON cone of uncertainty)
  - `current_wx`, `cityforecast`, `districtnowcast`, `stationnowcast`, `districtwarning`, `basinqpf`
- Marine, cyclone, sea-area and coastal warnings are available as structured JSON/GeoJSON — avoids PDF
  scraping. RSMC New Delhi also publishes National/RSMC/GMDSS/Fishermen-Warning bulletins.

### ISRO / MOSDAC / Bhuvan / NRSC
- **MOSDAC** (`mosdac.gov.in`) — SSO registration required, no separate API key. Python `mdapi` client at
  `mosdac.gov.in/software/mdapi.zip`; configure username/password + `datasetId` in `config.json`; needs
  `requests`. Find the exact `datasetId` at `mosdac.gov.in/catalog/satellite.php`. Confirmed Oceansat-3
  (EOS-06) products: OCM-3 chlorophyll/AOD/Kd (e.g. `E06OCM_L2C_AD`), SCAT-3 winds; HDF5/NetCDF. General
  users get L1 with **3-day latency**; L2+ nearer real-time. **No operational Oceansat-3 SST** (SSTM
  fault — see `METHODS.md`).
- **Bhuvan / NRSC** — WMS/WFS geospatial layers (incl. a PFZ layer mirror) via `bhuvan-app1.nrsc.gov.in`;
  `bhoonidhi.nrsc.gov.in` for EO product download. Good for base map layers and bathymetry overlays.

### NIOT — in-situ buoys (ground truth for the Reliability layer)
- OMNI buoy network: exactly **12 deep-sea OMNI buoys** (7 Bay of Bengal, 5 Arabian Sea), plus 4 coastal
  buoys and 2 tsunami buoys (NIOT OMNI-RAMA portal). Hourly real-time surface met + subsurface
  (T/S/currents to 500 m) + waves (selected buoys). NIOT deploys/maintains; INCOIS manages the data.
  Open-data policy (2018) for buoys outside the EEZ via the OMNI-RAMA joint portal. **These are what you
  backtest forecasts against for the Reliability Horizon.**

### CMLRE / NCCR
- CMLRE (deep-sea/biodiversity, e.g. FORV Sagar Sampada cruise data) and NCCR (coastal water
  quality/pollution) — mostly non-API, report/portal-based. Realistic hackathon use: cite as authoritative
  context for the causal/ecosystem agent; ingest any downloadable datasets manually. **Don't promise live
  integration.**

### International fallbacks (essential for resilience and for waves)
- **Copernicus Marine (CMEMS)** — free account, no key; `pip install copernicusmarine`;
  `copernicusmarine login` then `subset`. Confirmed dataset IDs (rotate ~biannually — run
  `copernicusmarine describe` before the finale):
  - Waves: `cmems_mod_glo_wav_anfc_0.083deg_PT3H-i` (VHM0, VTPK, VMDR, swell components; 1/12°, 3-hourly,
    10-day) — **your reliable wave source.**
  - Currents/physics: `cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m` (uo/vo daily), plus thetao/so variants.
  - Chlorophyll: `cmems_mod_glo_bgc-pft_anfc_0.25deg_P1D-m` (chl, 0.25°, daily).
- **NASA OceanColor / PODAAC / Earthdata** (MODIS-Aqua, VIIRS, GHRSST SST — Earthdata login), **NOAA
  ERDDAP / CoastWatch**, **NOAA Tides & Currents API** (`api.tidesandcurrents.noaa.gov`), **GEBCO**
  bathymetry grid, **Open-Meteo Marine API** (no key — fastest wave/weather fallback for a demo).
- **Boundaries / MPA / AIS:** `marineregions.org` (EEZ v12, WFS at `geo.vliz.be/geoserver`, CC-BY),
  Protected Planet / WDPA (MPA polygons, monthly updates), Global Fishing Watch (AIS/effort layers),
  OpenStreetMap coastline. For the India–Sri Lanka IMBL, use the **agreed 1974/76 boundary geometry**,
  not a computed median.

## 3. Ingestion classification — what's realistic to wire up in days

| Source | Access type | Auth | Format | Realistic to wire in days? |
|---|---|---|---|---|
| INCOIS ERDDAP | OPeNDAP/REST (griddap/tabledap) | none | NetCDF/CSV/JSON | **Yes — start here** |
| IMD `api.imd.gov.in` | REST/JSON + GeoJSON | none | JSON | Yes |
| CMEMS | Python toolbox | free account | NetCDF/Zarr | Yes |
| Open-Meteo Marine | REST/JSON | none | JSON | Yes (demo fallback) |
| marineregions/WDPA/GEBCO | bulk download (shp/geojson/tif) | none | vector/raster | Yes (load once into PostGIS) |
| INCOIS PFZ/OSF | WMS + text scrape | none | WMS/text/PDF | Partial — parse text |
| MOSDAC | Python `mdapi` client | SSO registration | HDF5/NetCDF | Partial — registration + latency |
| NIOT OMNI buoys | portal (INCOIS-managed) | portal | NetCDF/tabular | Partial — for backtesting |
| CMLRE/NCCR | reports/portal | varies | PDF/tabular | Manual only |

## 4. Resilient-ingestion design

- Adapters emit a common record: `{variable, value, lat, lon, valid_time, source, issued_time, unit}`.
- Use `xarray` + `dask` for NetCDF/GRIB; persist subsetted fields as Zarr / Cloud-Optimized GeoTIFF;
  precompute per-coastal-node time series into PostGIS + TimescaleDB.
- Cache by natural cadence: PFZ 3×/week; OSF every 12 h; IMD bulletins a few times/day; CMEMS waves every
  12 h.
- **Degrade gracefully:**
  - INCOIS SST down → `NOAA_AVHRR_AMSR` → CMEMS `thetao`
  - MOSDAC chlorophyll stale → CMEMS BGC `chl`
  - INCOIS waves unavailable → CMEMS `VHM0` → Open-Meteo
- Always surface data provenance + age. **Refuse/abstain rather than serve stale safety advice.**

## 5. Caching / offline design

- Precompute coastal-node time series nightly; tile map layers; memoize agent results keyed by
  `(intent, location-cell, valid-hour)`.
- Offline/degraded mode: cache last-known advisory + a small on-device model for cached-data Q&A;
  explicit "data age" banner in the UI.

## 6. Scalability roadmap (as pitched in the PPT)

Four phases on the same architecture (no rebuilds):
1. **Prototype** — full agent architecture live, sample datasets, verification engine demonstrated.
2. **Regional pilot** — live IMCOIS/IMD data feeds, one coastal region, ML models calibrated.
3. **Operational scale** — multi-region expansion, more data sources, cloud-scale execution.
4. **National layer** — pan-India coverage, multiple stakeholder groups, shared national infrastructure.

## 7. Cost & compute viability (as pitched in the PPT)

- Reuses existing marine data products — not a full ocean simulation built from scratch.
- Selective agent activation only (not continuous full-scale processing) → cached, reusable evidence
  keeps cloud cost predictable rather than high/unpredictable.

## 8. Highest-risk assumptions to validate on Day 1 (gap-closure report)

1. MOSDAC registration/latency — if blocked, rely on CMEMS + INCOIS.
2. INCOIS wave/OSF griddap ID — confirm live or use CMEMS `VHM0`.
3. OMNI buoy historical access for backtesting — if unavailable, ship reliability as a documented design
   with a synthetic-hindcast demo.
4. Bhashini API quotas — cache and pre-generate demo audio.
5. IMBL geometry correctness — use the agreed 1974/76 boundary, not a generated median line.
