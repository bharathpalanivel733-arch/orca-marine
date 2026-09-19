# PROGRESS.md — Status Tracker

This file is meant to be **updated as you build**. It currently reflects only what's stated in the two
source documents (the SIH PPT and the gap-closure report) as of this write-up — treat the PPT's
"feasibility" timeline claims as pitch claims, not verified build status, until confirmed against the
actual codebase.

---

## Phase 0 — Foundations & Day-1 risk validation (2026-09-18)

Scope: `PLAN.md` Phase 0 only. No later phase was started; no marine data source, agent
or LLM provider is called by any code in this commit.

### Completed

- [x] **0.1 Repo scaffold** — `apps/web` (Next.js 15 + TS + Tailwind v4 + shadcn/ui),
      `services/api` (FastAPI + Pydantic v2), `services/agents`, `services/ingest`,
      `packages/schemas`, `infra`, plus root `README.md`, `.gitignore`, pnpm workspace.
- [x] **0.1 Shared contracts** — `packages/schemas/orca_schemas` (Pydantic) is the single
      source of truth; `pnpm schemas:generate` exports JSON Schema and compiles it to
      `generated/types.ts`, which `apps/web` imports. The web status page consumes the
      generated `ServiceHealth` / `ComponentHealth` / `HealthStatus` types directly, so
      API and UI cannot drift.
- [x] **0.2 Dev stack defined** — `infra/docker-compose.yml`: `timescale/timescaledb-ha:pg16`
      (PostGIS + TimescaleDB + pgvector in one database), Redis 7, MinIO; `infra/db/init/`
      creates the four extensions and the `evidence` / `geo` / `provenance` schemas.
      `scripts/verify-stack.sh` (`pnpm db:verify`) is the acceptance check.
- [x] **0.3 Config & secrets** — `.env.example` covering infra, sources, speech and LLM keys
      (all blank-safe); `config/models.yaml` encoding the PLAN §1.1 routing table
      (planner/verifier `claude-opus-5`, synthesizer `claude-sonnet-5`, extractor
      `claude-haiku-4-5`, Groq fallback disabled, per-query cost budget). Loaded and
      validated at startup — a missing required role fails fast.
- [x] **0.5 Observability** — structlog JSON logging, a `run_id` minted or accepted per
      request via the `X-Run-Id` header, bound to a context var so every log line carries
      it, echoed on the response; optional OpenTelemetry (off by default, no collector
      needed) plus a `span_for_node()` helper for future DAG nodes.
- [x] Health endpoints — `/healthz` (liveness) and `/readyz` (concurrent dependency probes
      with a deterministic worst-case rollup).
- [x] Toolchain — ruff, mypy (strict), pytest, eslint, tsc wired into
      `pnpm lint` / `typecheck` / `test` / `verify`, plus `pnpm setup:py`.

### Evidence (all commands run locally on 2026-09-18)

| Check | Command | Result |
|---|---|---|
| Python tests | `pnpm test` | **24 passed** (schemas 6, api 11, agents 2, ingest 2, config 5) |
| Lint | `pnpm lint` | eslint clean; `ruff check services packages` → All checks passed |
| Types | `pnpm typecheck` | `tsc --noEmit` clean; `mypy --strict` → no issues in 9 source files |
| Aggregate | `pnpm verify` | exit 0 |
| Web build | `pnpm --filter @orca/web build` | compiled in 15.8 s, 4 static pages, 102 kB shared JS |
| Contract generation | `pnpm schemas:generate` | 4 model schemas + bundle → `generated/types.ts` (5 exports, compiles clean) |
| API runtime | `uvicorn orca_api.main:app` + curl | `/healthz` → `{"status":"ok"}`; `X-Run-Id: orca_7ee39b19…` minted; inbound `X-Run-Id: orca_manual_check` preserved unchanged; JSON logs carry the run id on both request lines |
| Degradation | `curl /readyz` with no stack running | `status: "down"`, each of postgres/redis/object-store reported `down` with `"tcp connect timed out after 2.0s"` — no false "ok" |

### 0.4 Day-1 risk spikes — EXECUTED 2026-09-18

Tooling: `scripts/spikes/run_spikes.py` (`pnpm spikes` / `make spikes`), time-boxed per
spike, credential-independent, writing `artifacts/spikes/latest.json` with every probe's
URL, HTTP status and elapsed time. Verdicts: `GO` (capability present), `NO_GO` (probed,
absent), `BLOCKED` (needs a credential or manual step — never guessed), `ERROR` (the probe
itself failed; unknown, not a finding). Run result: **2 NO_GO, 3 BLOCKED, 0 ERROR**, exit 0.

| Spike | Verdict | Evidence | Decision |
|---|---|---|---|
| **(a) MOSDAC registration / latency** | **BLOCKED** | `mosdac.gov.in` 200, `/catalog/satellite.php` 200, `HEAD /software/mdapi.zip` 200 — service is up. `MOSDAC_USERNAME`/`MOSDAC_PASSWORD` unset, so L1 latency is unmeasured. | MOSDAC stays a Phase 1.8 enhancement behind CMEMS + INCOIS. No Oceansat-3 SST claimed regardless (SSTM non-operational). |
| **(b) INCOIS wave / OSF griddap id** | **NO_GO** | ERDDAP up (200). **Control search `sst` → 4 datasets**, proving the query shape works; `wave`, `swell`, `osf`, `indofos` each → HTTP 404 *"your query produced no matching results."* | **Confirms the DEPLOYMENT.md §2 caveat.** CMEMS `cmems_mod_glo_wav_anfc_0.083deg_PT3H-i` (VHM0) is the **primary** wave source in Phase 1.4, not a fallback. INCOIS OSF waves only via bulletin/WMS scraping (Phase 1.7, lower priority). |
| **(c) NIOT OMNI buoy history** | **BLOCKED** | `niot.res.in` 200; ERDDAP `searchFor=buoy` 200; `incois.gov.in/portal/datainfo/buoys.jsp` 404. Paired forecast+observation history sits behind the account-gated OMNI-RAMA portal. | Request portal access now (long lead time). PLAN.md 10.1 trigger stands: no hindcast by Day 4 → ship the Reliability Horizon as a rigorous design with a synthetic hindcast. |
| **(d) Bhashini quota** | **BLOCKED** | `bhashini.gov.in` 200; `meity-auth.ulcacontrib.org` 404 at root (host resolves and serves). `BHASHINI_USER_ID`/`BHASHINI_ULCA_API_KEY` unset, so no ASR/TTS call was made and quota is unmeasured. | Register for ULCA credentials. Build Phase 7 against the fallback chain regardless (self-hosted IndicWhisper / Indic-Parler-TTS + pre-generated audio cache) — that is what makes the demo quota-proof. |
| **(e) Agreed 1974/76 IMBL geometry** | **NO_GO** | marineregions WFS capabilities 200. Layers found: `eez`, `eez_boundaries`, `eez_12nm`, `eez_24nm`, `eez_iho`, `ecs_boundaries`, `iho`, … — **all EEZ/derived geometry, none the bilateral treaty line.** | **Phase 2 remains blocked.** Source the treaty geometry from the 1974/76 agreement text (UN DOALOS / MEA treaty records), load into PostGIS as authoritative reference data, and pin known Palk Strait coordinates in a regression test. A computed median line must never be substituted (METHODS.md §3). |

**A tooling defect was found and fixed, not worked around.** The first run returned
`ERROR` on all five spikes with `CERTIFICATE_VERIFY_FAILED: unable to get local issuer
certificate`, which looked like the endpoints being down. They were not: `curl` reached
them (HTTP 200) because Windows `curl` uses Schannel, which fetches a missing intermediate
CA over AIA. Python/OpenSSL does not, and an up-to-date certifi bundle did not help either
— several of these government hosts serve an **incomplete certificate chain**. The harness
now builds its TLS context through `truststore`, delegating chain building to the OS.
**Certificate verification is never disabled**; a host that cannot be verified yields
`ERROR`, because trusting an unverified government endpoint would make every result
worthless as evidence.

Spike (b) is also self-validating: ERDDAP answers a zero-match search with HTTP 404, which
is indistinguishable from a malformed URL by status code, so a control query that must
match runs first. Without it the NO_GO could have been an artifact of my own URL.

### Dev stack — VERIFIED 2026-09-18 (second attempt, after the host was fixed)

The WSL2/virtualization blocker recorded earlier was resolved on the dev machine.
Re-checked from scratch: `wsl --status` → `Default Distribution: docker-desktop,
Default Version: 2`; `docker info` → `Server Version: 29.8.0`, kernel
`6.18.33.2-microsoft-standard-WSL2`; `docker ps` → exit 0.

**One fix was required to bring the stack up:** `minio/minio:latest` on Docker Hub is no
longer publicly pullable (`pull access denied for minio/minio, repository does not exist
or may require 'docker login'`), which aborted the whole `compose up`. The image is now
sourced from MinIO's own registry, `quay.io/minio/minio:latest`, in
`infra/docker-compose.yml`. The database image needed no change:
`timescale/timescaledb-ha:pg16` does carry all three required extensions, so the earlier
fallback plan (`postgis/postgis` + build step) was not needed.

`pnpm db:up` → all three containers **Started → Healthy**, exit 0.
`pnpm db:verify` → **stack verified: all checks passed**, exit 0.

| Component | Version | Functional proof |
|---|---|---|
| PostgreSQL | **16.15** (Ubuntu 16.15-1.pgdg22.04+2) | `SELECT version()` |
| PostGIS | **3.6.4** | `ST_Distance` on `geography` between two points 0.1° apart at 13.1°N → **10844 m** (correct for that latitude) |
| TimescaleDB | **2.30.1** | `create_hypertable('evidence._ts_probe','t')` → `(1,evidence,_ts_probe,t)`; probe table dropped |
| pgvector | **0.8.6** | `'[1,2,3]'::vector <-> '[4,5,6]'::vector` → **5.196152422706632** (= √27) |
| pg_trgm | 1.6 | present in `pg_extension` |
| Schemas | — | `evidence`, `geo`, `provenance` all created by the init script |
| Redis 7 | — | `redis-cli ping` → `PONG` |
| MinIO | — | `/minio/health/live` → live; container healthy |

API readiness against the live stack: `GET /readyz` → `{"status":"ok"}` with
`postgres` 43.21 ms, `redis` 36.39 ms, `object-store` 35.40 ms — all `ok`. The same
endpoint returned `down` for all three before the stack was up, so the probe reflects
reality rather than a hardcoded result.

**PLAN.md Phase 0.2 acceptance criterion is now met.**

### NOT verified — do not claim these

- **No data source is live.** INCOIS, IMD, CMEMS, MOSDAC, NIOT, Bhashini and every LLM
  provider are unconfigured and uncontacted. No credentials exist in this environment.
- **Credential-dependent measurements remain unmeasured**: MOSDAC L1 latency and Bhashini
  ASR/TTS quota. Both spikes report BLOCKED with the missing variable named; neither value
  has been guessed or estimated anywhere in this repo.
- **OMNI buoy bulk history is unproven** — reachability was probed, portal access was not.

### Risks

- **Python 3.14 on the host vs 3.12 in containers.** The venv installs fine on the host's
  3.14 today, but the Phase 1 science stack (xarray, GeoPandas, Shapely, psycopg) has
  reliable wheels on 3.12, which is what `services/api/Dockerfile` pins. Expect host/
  container divergence at Phase 1; the container is authoritative.
- ~~`timescale/timescaledb-ha:pg16` is an assumption~~ — **confirmed 2026-09-18**: the
  image ships PostGIS 3.6.4, TimescaleDB 2.30.1 and pgvector 0.8.6 on PostgreSQL 16.15.
  Registry risk is real though, and bit us once: `minio/minio` on Docker Hub became
  non-pullable. Images are worth pinning by digest before the finale.
- **Readiness probes are TCP-only.** They prove a port answers, not that the database is
  usable, and the payload says exactly that. Phase 1 should replace them with a real query
  once a driver is in the dependency set.
- `make` and `psql` are absent on this machine; pnpm scripts and `docker compose exec` are
  the working entrypoints. PLAN 0.2's literal "`make dev`" wording is satisfied by
  `pnpm dev`.

### Blockers

1. ~~WSL2 / virtualization unavailable~~ — **RESOLVED 2026-09-18.** Docker engine,
   database stack and `pnpm db:verify` all confirmed working (see the verified section
   above). No longer a blocker.
2. **No credentials** — blocks spikes (a) MOSDAC SSO, (d) Bhashini quota test. Spikes (b)
   INCOIS wave/OSF griddap ID and (c) OMNI buoy history need no key and can be run as soon
   as someone takes them; they are keyless HTTP checks against public catalogs.
3. **IMBL geometry not sourced** — spike (e). Still a hard blocker for Phase 2, unchanged.

### Next phase

**Phase 1 — Ingestion layer** (`PLAN.md` Phase 1.1–1.12), starting with 1.1 (the common
record contract) and 1.2 (INCOIS ERDDAP adapter), which the plan names as the place to
start. Phase 1 can begin despite the Docker blocker: 1.1–1.9 (record contract, adapters
against the keyless INCOIS ERDDAP / IMD / Open-Meteo endpoints) need no database. But
1.10–1.12 (TimescaleDB storage, cadence-aware cache, degradation chain) **cannot be
verified** until the WSL2 blocker above is cleared, and must not be marked done before
then.

---

## Phase 1 — Ingestion layer (in progress)

### 1.1 Adapter interface + common record contract — DONE (2026-09-18)

- [x] **Common record** (`packages/schemas/orca_schemas/ingest.py`) — `ObservationRecord`
      carrying `{variable, value, unit, lat, lon, depth_m, valid_time, issued_time,
      source, dataset_id, quality, kind, license}`. Three invariants are enforced by the
      model rather than trusted to adapters:
      1. **Canonical units** — `CANONICAL_UNITS` fixes one unit per variable and the
         validator rejects anything else, so a source publishing wave height in
         centimetres must convert inside its adapter instead of handing ORCA a number a
         safety threshold would later misread as metres.
      2. **Missing data is representable** — `value` may be `None` only when quality is
         `missing`, and non-finite values (NaN/Inf, i.e. leaked NetCDF fill values) are
         rejected outright.
      3. **Absolute time** — naive datetimes are rejected; staleness needs unambiguous
         instants.
- [x] **Canonical vocabulary** — `MarineVariable` (14 variables), `DataQuality`, and
      `MeasurementKind` (observation / analysis / forecast, needed so Phase 10.1 can pair
      forecasts with buoy observations).
- [x] **Query types** — `BoundingBox` (with `contains` / `intersects`) and `TimeWindow`
      (half-open, ordering enforced).
- [x] **Per-source cadence metadata** — `Cadence(period, grace)` with `deadline` and
      `is_stale()`. Staleness is arithmetic on the clock, never a judgement call and never
      an LLM decision.
- [x] **Source registry metadata** — `SourceDescriptor` (id, authority_rank, variables,
      coverage, cadence, license, requires_auth, attribution). `authority_rank` is what the
      degradation chain will order fallbacks by.
- [x] **Adapter interface** (`services/ingest/orca_ingest/adapter.py`) — `SourceAdapter`
      ABC with `descriptor` and `async fetch(FetchRequest) -> FetchResult`, plus
      deterministic capability checks (`supports` / `covers` / `unsupported_variables`)
      that answer "could this source serve this" without spending a network call.
      `SourceUnavailableError` keeps "the source did not answer" distinct from "there is
      no data there" — the degradation chain reacts to the first and not the second.
      Partial success is reported through `missing_variables` / `warnings`, not raised,
      because the Phase 6.3 sufficiency gate needs to see an unanswered ask.
- [x] TypeScript contracts regenerated from the Pydantic source (16 exports).

**Fixed along the way:** the root `pnpm test` ran one pytest session from the repo root,
where the per-package `[tool.pytest.ini_options]` blocks are not read, so async adapter
tests failed with "async def functions are not natively supported". A root `pytest.ini`
now sets `asyncio_mode = auto` and the canonical testpaths. Also removed duplicate
generated TS aliases (`DataQuality1`) by making every model reachable from the schema
bundle root.

**Evidence:** `pnpm verify` exit 0 — **62 tests passed** (was 24; +38 for this task),
ruff clean, `mypy --strict` clean across 10 source files, `tsc --noEmit` clean, web build
compiled. Async tests confirmed executing (not skipped) via
`pytest -v` → `asyncio: mode=Mode.AUTO`, all `PASSED`.

**No network call is made by any code in 1.1**, and no source is configured as live.

### 1.2-1.12 Adapters, degradation, caching, persistence — DONE (2026-09-18)

**Two findings that change the project's assumptions.** Both are recorded as corrections
in `DEPLOYMENT.md` §2 because they would otherwise be discovered on stage:

1. **Every INCOIS ERDDAP dataset is a historical archive, not a live feed.** Verified via
   each dataset's own `info` document: `incois_tmi_3day_datasets` ends **2014-12-31**,
   `NOAA_AVHRR_AMSR_datasets` **2011-10-04**, `incois_oceansat2_datasets` **2020-05-01**,
   `ascat_daily_datasets` **2023-05-21**, `Indian_ARGO_Floats` **2025-04-23**. INCOIS
   ERDDAP therefore **cannot answer "is it safe tomorrow"**; its value is the hindcast
   archive the Reliability Horizon needs (10.1) and climatology for the causal engine.
2. **IMD `api.imd.gov.in` is no longer keyless.** Every endpoint returns
   `401 {"error":"API key missing"}`. The docs described it as requiring no key. IMD is
   now a credential-dependent source and needs `IMD_API_KEY`.

Consequence: **Open-Meteo Marine is currently the only keyless live numeric source**, and
CMEMS (credentialed) is the primary wave source per spike (b). The architecture handles
this correctly rather than hiding it — the staleness gate rejects the ERDDAP archives for
present-day queries automatically.

#### Delivered

- [x] **1.1 contract + interface** (previous commit) — `ObservationRecord`, `FetchRequest`,
      `SourceAdapter`, cadence metadata.
- [x] **1.2 INCOIS ERDDAP adapter** — discovers dimensions, variables, licence and time
      coverage from each dataset's `info` document; **nothing hardcoded**. Handles the
      `zlev`/`depth` extra axes (pinned, not dropped — dropping one misaligns every later
      constraint) and the 0–360 longitude grid. Refuses out-of-coverage windows with an
      explanation. ERDDAP's 404-for-no-matches is treated as empty, not as an outage.
- [x] **1.3 IMD**, **1.4 CMEMS**, **1.8 MOSDAC**, **1.9 NIOT OMNI** — credential-gated
      scaffolds that raise `SourceUnavailableError` naming the missing variable. "Not
      registered" and "not built yet" are distinct reasons, because they call for
      different actions. MOSDAC deliberately does **not** declare Oceansat-3 SST.
- [x] **1.5 Open-Meteo Marine adapter** — live-verified. Maps `°`→`degree`, turns nulls
      into `quality=missing` records (never 0.0, which would read as a flat calm), takes
      coordinates from the response rather than the request, and refuses a field whose
      upstream unit is unexpected rather than guessing a conversion.
- [x] **1.6 INCOIS PFZ**, **1.7 INCOIS OSF** — scaffolds stating they have no JSON API
      (text/WMS and bulletins only); PFZ carries the 3×/week (56 h) cadence.
- [x] **1.10 raw-payload archiving** — SHA-256 content-addressed, idempotent, filesystem
      and S3/MinIO backends. This is what deterministic replay (6.4) re-runs from.
- [x] **1.10 TimescaleDB persistence** — `evidence.observations` hypertable with
      provenance columns, a natural-key upsert (re-ingest updates, never duplicates) and
      DB-level CHECK constraints mirroring the Pydantic invariants.
- [x] **1.10 object storage** — MinIO via boto3; **Zarr implemented**.
      **COG is NOT implemented** and raises `NotImplementedError` — it needs rasterio/GDAL
      and has no consumer yet. A stub that wrote something else would be worse than a gap.
- [x] **1.11 degradation policy** — deterministic ordering by `authority_rank`; every
      attempt recorded as a `SourceAttempt` with outcome and reason, so a decision can show
      what was tried and rejected, not just what won.
- [x] **1.12 cadence-aware cache + staleness flags** — expiry is the source's own cadence,
      measured from *issue* time, not cache-write time.

#### Evidence

| Check | Result |
|---|---|
| `pnpm verify` | **exit 0** — ruff clean, `mypy --strict` clean (24 files), `tsc` clean |
| Unit tests | **110 passed** (was 62), 10 deselected opt-in |
| `pytest -m stack` | **7 passed** against the real TimescaleDB + MinIO |
| `pytest -m live` | **3 passed** against real endpoints |
| `pnpm db:migrate` | applied to `localhost:5432/orca` |

Live evidence: Open-Meteo returned current wave forecasts for Chennai with plausible
values and correct units; ERDDAP discovery returned real dimensions and a coverage end in
2014; a present-day ERDDAP query was refused with the archive explanation.

**The staleness guarantee is tested, not asserted** (`test_degradation.py`):
a stale authoritative source falls through to a fresh fallback; when *every* source is
stale the result is **empty with recorded reasons** rather than old data; staleness is
judged against each source's own cadence (6 h is fresh for a 3×/week advisory, stale for
an hourly buoy); and stale data is reachable only through an explicit opt-in that still
exposes its age.

#### Not done / deliberately deferred

- **COG output** — not implemented (see above).
- **Redis cache backend** — the `CacheBackend` interface is there and Redis is running,
  but only the in-memory backend is built; cross-process sharing has no consumer yet.
- **No credentialed source has ever been contacted.** IMD, CMEMS, MOSDAC and NIOT OMNI are
  scaffolds. Nothing in this repo has authenticated to any of them.
- **PFZ/OSF parsing** — the scaffolds fetch nothing; text/WMS parsing is real work that
  needs a decision on how much bulletin scraping is worth before the finale.

### Next: Phase 2 — geospatial & geofencing

Still hard-blocked on the agreed 1974/76 India-Sri Lanka IMBL geometry (spike (e) NO_GO).
Phase 2 must not start with a computed median line. The unblocked alternative is Phase 4
(decision kernels), which is pure deterministic Python over the record contract that now
exists.

---

## Phase 2 — PostGIS geospatial & predictive geofencing (2026-09-18)

### The IMBL blocker is CLEARED — with authoritative sources, not a median line

Spike (e) had returned NO_GO because marineregions publishes only EEZ and derived
geometries. The boundary was found instead in the **UN DOALOS Delimitation Treaties
Infobase**, which holds the deposited texts of all three agreements:

| Agreement | Signed | Content | SHA-256 (recorded, re-verified live) |
|---|---|---|---|
| `LKA-IND1974BW.PDF` | 26/28 Jun 1974 | Art. 1: six positions, Palk Strait to Adam's Bridge | `2cf10cb6…` |
| `LKA-IND1976MB.PDF` | 23 Mar 1976 | Art. 1: thirteen positions, Gulf of Mannar; Art. 2: eight, Bay of Bengal | `254d1109…` |
| `LKA-IND1976TP.PDF` | 22 Nov 1976 | Art. 1: extension from 13 m to trijunction Point T | `4118dbd7…` |

All three state the boundary is **arcs of great circles** between the listed positions,
so segments are geodesics. **28 positions** are transcribed into
`services/geo/orca_geo/imbl.py` with per-position treaty and article citations.

**Two continuity checks confirm the texts describe one boundary** (both asserted in
tests): 1976 `1m` == 1974 Position 6, and 1976 `1b` == 1974 Position 1. The shared
vertices are stored once, so the geometry contains no zero-length segment.

**Documented source anomaly.** 1976MB Article 1 writes position 4 m as
`08° 40'.0 N 79° 18'.2 N` — the longitude carries `N` where every other position carries
`E`. Corrected to `E`, recorded in `SOURCE_ANOMALIES`, surfaced by `imbl_provenance()`
and carried into the PostGIS row, so a reviewer can challenge the judgement rather than
having to rediscover it.

### Independent corroboration

The transcription is validated against geography that does not come from this code. Seven
known points classify correctly, and **Kachchatheevu lands 1.39 km on the Sri Lankan
side** — matching the historical fact that the 1974 line was drawn immediately west of
the island to cede it to Sri Lanka. A wrong coordinate would not reproduce that.

| Indian side | Sri Lankan side |
|---|---|
| Rameswaram, Point Calimere, Vedaranyam, Tondi | Kachchatheevu, Talaimannar, Kankesanthurai (Jaffna), Mannar town |

### Delivered

- [x] **2.1 reference layers** — `geo.boundaries` / `eez` / `protected_areas` /
      `seasonal_bans` / `coastline` / `bathymetry_tiles`, all GIST-indexed
      (`0002_geo.sql`). IMBL **loaded**; EEZ WFS request **implemented**; MPA (WDPA),
      seasonal bans, coastline (OSM) and bathymetry (GEBCO) are **documented ingestion
      paths** in `reference.py` with endpoint, access method and licence. An unbuilt
      loader raises with the next step — it never returns an empty layer, which for a
      protected-area layer would read as "no restrictions here".
- [x] **2.2 containment and metric distance** — PostGIS `ST_Distance` on `geography` is
      authoritative; `pyproj` gives the same answer in-process without a round trip.
- [x] **2.3 warning bands** — 5 km amber / 2 km red by default, configurable, with
      `ST_Buffer` band geometry so the map draws what the warnings are computed from.
- [x] **2.4 predictive drift** — vessel velocity + forecast current; crossing time found
      by stepping then **bisecting**, so the answer is step-size independent (asserted).
      Engine-off pure-current drift is supported, since that is how crossings happen.
      Every forecast carries its own assumptions (current held constant, no wind leeway).
- [x] **2.5 solver constraints** — MPA and seasonal bans as hard/soft constraints;
      year-wrapping closed seasons handled; an MPA with no seasonal window is closed
      year-round, never never-closed.
- [x] **2.6 Palk Strait regression tests** — the table above, plus band-threshold,
      crossing-detection and transcription-integrity tests.

### Evidence

| Check | Result |
|---|---|
| `pnpm verify` | **exit 0** — ruff clean, `mypy --strict` clean (31 files), `tsc` clean |
| Unit tests | **172 passed** (was 110); 62 new in `services/geo` |
| `pytest -m stack` | **17 passed** against real PostGIS |
| `pytest -m live` | treaty PDFs re-downloaded; **all three SHA-256 match** |

**A real discrepancy was found, understood and bounded rather than hidden.** The PostGIS
and pyproj distances disagree by up to ~8 m on 28 km (0.03%) and ~2 m on 726 m (0.3%),
because `ST_ClosestPoint` finds the nearest point in planar degree space and the two
differ slightly in spheroid handling. The tolerance reflects that; separately,
`test_distance_disagreement_never_changes_a_warning_band` asserts the property that
actually matters — that the gap can never move a vessel between bands.

### Not done

- **MPA, coastline and bathymetry geometry is not loaded.** Paths are documented and the
  schema is ready; the WDPA licence restricts redistribution so the file is not vendored.
- **Seasonal ban dates are not loaded.** They are per-state notifications that change;
  the fixtures in tests are explicitly labelled as fixtures, not authoritative dates.
- **EEZ is implemented but not loaded** — the WFS request is built, nothing has fetched it
  into the table yet.
- Drift does not model wind leeway, tidal variation along track, or course change.

### Next phase

**Phase 4 — decision kernels** (safety score, Pareto fishing zones, isochrone-A* routing).
Phase 3 (evidence/RAG) is also unblocked. Phase 2's geometry is now available as the hard
constraint layer that Phase 4.4's route cost surface needs.

---

## Phase 3 — Structured evidence & RAG (2026-09-19)

### A real bug in Phase 1 was found and fixed

Writing the `as_of` tests exposed a design fault in the Phase 1 schema. The observations
natural key was `(source, variable, valid_time, lat, lon, depth_key)` — it omitted
`issued_time`, so when a forecast was **revised the new issue overwrote the old row** and
the superseded forecast was destroyed.

That makes bitemporal querying impossible, and it would have quietly broken Phase 6.4:
replaying yesterday's decision would have used today's better forecast, so **every replay
would have looked correct** and the trust layer would have been worthless. Migration
`0004_observations_bitemporal.sql` adds `issued_time` to the key. Re-ingesting the *same*
issue still updates in place — the Phase 1 "no duplicates on re-fetch" guarantee is
preserved and its test still passes — while a revision is now a new row.

### Delivered

- [x] **3.1 tool/dataset registry** (`registry.py`) — capability metadata per PLAN:
      variables, coverage bbox, cadence, latency, authority_rank, reliability_prior, cost,
      licence, plus temporal coverage. `select()` filters on hard constraints and orders
      deterministically by `(authority_rank, -reliability_prior, cost, dataset_id)`.
      **Every rejection carries a reason**, so a plan can show why INCOIS was not used.
      The entries encode verified findings: the INCOIS archives are marked with their real
      end dates and are rejected for present-day questions but selected for a 2013 one;
      IMD and CMEMS are rejected when their credentials are absent rather than failing later.
- [x] **3.2 `as_of` structured querying** (`structured.py`) — `DISTINCT ON` over the
      bitemporal table returns the latest issue **at or before** `as_of`, never the latest
      overall. `as_of` is a required argument, not defaulted to now.
- [x] **3.3 corpus ingestion** (`corpus.py`, migration `0003_corpus.sql`) — documents for
      PFZ advisories, ocean-state forecasts, IMD warnings, cyclone bulletins, NDMA SOPs,
      fishing-ban notifications, MPA rules and ABIS bulletins. Provenance fields are
      **NOT NULL in the schema and required by the model**: a passage that cannot be dated
      or attributed is refused at ingest rather than retrieved later and unexplainable.
      Paragraph-aware chunking; re-ingest replaces chunks so a revised advisory leaves no
      stale passages behind.
- [x] **3.3 BGE-M3 embeddings** (`embeddings.py`) — `BAAI/bge-m3`, 1024-d, L2-normalised,
      behind the optional `embeddings` extra. The `vector(1024)` column pins the dimension,
      so a wrong-sized model cannot be stored (asserted in a test). A
      `DeterministicEmbedder` exists for hermetic tests and **declares `is_semantic =
      False`** so it can never be mistaken for the production path.
- [x] **3.4 hybrid retrieval** (`retrieval.py`) — lexical (`tsvector` + `ts_rank_cd`) and
      vector (pgvector HNSW, cosine) fused with **reciprocal rank fusion** (k=60). RRF is
      used rather than a weighted score sum because BM25 and cosine are not on comparable
      scales; fusing ranks avoids inventing a normalisation.
- [x] **3.4 hard gates** — the temporal and regional conditions live **inside both SQL
      queries**, not as a post-filter. A post-filter would let an expired advisory occupy a
      top-k slot and then vanish; in SQL it is never a candidate. Gates: `issued_time <=
      as_of` (no future knowledge in a replay), `valid_until` honoured, optional `max_age`,
      region codes and PostGIS geometry containment.
- [x] **3.5 provenance on every item** — source, url, issued_time, computed age,
      authority_rank, licence and provenance id, on both structured and retrieved evidence.

### Evidence

| Check | Result |
|---|---|
| `pnpm verify` | **exit 0** — ruff clean, `mypy --strict` clean (38 files), `tsc` clean |
| Unit tests | **195 passed** (was 172) |
| `pytest -m stack` | **36 passed** against real PostgreSQL + PostGIS + pgvector |

Gate tests assert both directions — the admissible document is returned **and** the
inadmissible one is absent entirely: a 2023 advisory under a 2-day `max_age`, an expired
advisory past its `valid_until`, an advisory issued after the `as_of` anchor, a Gujarat
notification for a Palk Strait query, and a polygon-scoped document 900 km away. National
documents with no region codes stay admissible everywhere.

### Fixed along the way

- **PostgreSQL parameter typing.** Optional filters written as `%(p)s IS NULL` failed with
  `AmbiguousParameter: could not determine data type of parameter $12`. Every optional
  filter parameter now carries an explicit cast.
- **pytest on Windows.** The default `%TEMP%` basetemp failed the whole run with
  `PermissionError: [WinError 5] ... pytest-current` during symlink cleanup, despite every
  test passing. `pytest.ini` now sets a repo-local, gitignored `--basetemp`.
- **Model download TLS.** huggingface.co hits the same incomplete-certificate-chain problem
  as the Indian government endpoints; `BgeM3Embedder` injects the OS trust store before
  loading. Verification is never disabled.
- **Package-level test deselection (found in the Phase 3-5 audit, 2026-09-19).** Running
  `pytest services/evidence/tests` made rootdir resolve to that package, so the root
  `pytest.ini` addopts no longer applied and the opt-in suites were collected — including
  the `model` tests, which began downloading ~2 GB of weights and looked like a hang. The
  package's `pyproject.toml` now mirrors the root deselection; an explicit `-m` on the
  command line still overrides it, so `-m stack` and `-m model` work as before.

### Not done

- **BGE-M3 has never actually run.** `sentence-transformers` is installed and
  `BgeM3Embedder` is implemented, and the OS-trust-store fix was confirmed to reach
  huggingface.co (HTTP 200) — but the ~2 GB of weights were never downloaded in this
  environment, so the model has not been executed once. **Every passing test uses
  `DeterministicEmbedder`, which declares `is_semantic = False`.** The `-m model` suite
  (including a cross-lingual Tamil→English check) exists and is unrun. Nothing may claim
  BGE-M3 is verified until `pnpm test:model` passes.
- **No real corpus is loaded.** The ingestion path is built and tested, but no actual PFZ
  advisory, IMD bulletin, NDMA SOP, ban notification or MPA rule has been ingested — PFZ
  and OSF have no API (Phase 1.6/1.7 scaffolds) and IMD now needs a key. No external
  corpus or API integration is live.
- **Retrieval quality is unmeasured.** The tests prove the gates, the fusion and the
  plumbing. They do not prove the results are *good*; that needs the Phase 11.1 golden
  query set against a real corpus.
- **`reliability_prior` values are priors, not measurements** — Phase 10.1 replaces them
  with backtested CRPS/Brier scores.
- Redis-backed caching and a cross-encoder reranker are not built.

### Next phase

**Phase 4 — decision kernels.** Safety score, Pareto fishing zones, isochrone-A* routing,
over the record contract from Phase 1, the geometry from Phase 2 and the evidence access
from Phase 3.

---

## Phase 4 — Deterministic decision kernels (2026-09-19)

### The no-LLM guarantee is mechanical, not a policy statement

`services/kernels` has **no model runtime in its dependency tree** — it depends only on
`orca-schemas` and `typing-extensions`. Every kernel returns a `KernelResult` carrying
`value`, `inputs[]`, `formula_id`, `formula_version`, `staleness` and `abstain_reason`,
and `KernelResult.fingerprint()` hashes formula identity plus inputs plus value. If
anything — an LLM or otherwise — rewrote a number after the fact, the recorded inputs
would no longer reproduce it and `verify()` fails. That is asserted in tests rather than
asserted in prose.

### Delivered

- [x] **4.1 vessel profiles** — four classes with documented thresholds and a stated
      rationale each. METHODS.md §1's anchor is encoded exactly (FRP vallam: Hs < 1.5 m
      caution, < 2 m avoid) and the others scale by seakeeping. Per-vessel overrides are
      supported because a well-found boat is not the fleet average.
- [x] **4.2 boat-relative safety score** — 0-100 with an asymmetric confidence band,
      weighted over wave height, wind, swell period and squall risk, each normalised
      against the asking boat's thresholds and scaled by forecast reliability.
      **Hard-abstains** when a required driver is stale or missing, and when reliability
      falls below a floor. Reliability can only widen the band or force abstention — it
      can never raise a score.
- [x] **4.3 Pareto fishing zones** — returns the trade-off frontier, never one point.
      Compliance (IMBL, MPA, seasonal ban) is a **hard filter applied before ranking**,
      not an objective, so an illegal zone is never offered as a cheaper option with a
      caveat. PFZ evidence decays with advisory age, reaching zero weight after two
      three-times-weekly issue cycles.
- [x] **4.4 isochrone-A\*** — A* over a cost surface of `f(VHM0, current projected on
      heading, wind, depth)`, with an **admissible** heuristic derived from the grid's own
      cheapest step, so optimality is preserved. Geofenced cells are **removed from the
      graph, not penalised**: a penalty large enough to "usually" avoid arrest is still a
      route that crosses the line when the weather is bad enough. Benchmarked against a
      great-circle baseline, which returns `None` when the straight line would cross a
      geofence — the naive route is often not merely dearer but illegal.
- [x] **4.5 anomaly flags** — HAB (chlorophyll multiple *and* an absolute floor, so a
      spike over a negligible baseline is not a bloom), marine heatwave (the standard
      5-consecutive-days-above-p90 definition, not an invented one), and possible oil
      slick (**always low confidence** — ocean colour cannot separate a slick from sun
      glint; SAR can, and ORCA does not ingest it). Flags say "consistent with", never
      "there is".
- [x] **4.6 kernel contract** — one shape for every kernel, with abstention as a
      first-class result that must explain itself. The model rejects an abstention with a
      value, a value of `None` without a reason, and an abstention with no detail.

### Evidence

| Check | Result |
|---|---|
| `pnpm verify` | **exit 0** — ruff clean, `mypy --strict` clean (45 files), `tsc` clean |
| Unit tests | **269 passed** (was 195); 74 new in `services/kernels` |
| `pytest -m stack` | **53 passed** against the real stack |

Tests cover each vessel class (including a monotonicity check across all four), stale and
missing-input abstention, Pareto non-domination (no frontier member dominates another),
geofence-constrained routing, and kernel provenance including fingerprint tampering.

### Two design faults found by the tests and fixed

1. **Freeboard was deciding safety verdicts.** Scaling both class thresholds by the
   vessel's margin factor moved the score **27.8 points** on a ±10% factor, because the
   caution-to-avoid band is only ~0.5 m wide for small craft. The margin now adjusts the
   *hazard* instead, bounding its effect to the margin itself. The docstring had claimed
   the effect was small; the test proved it was not.
2. **A test asserted non-optimal routing.** It expected a detour around a 4 m band, but
   crossing genuinely costs 13.2 against a 13.52 detour — the router was right and the
   test was wrong. Replaced with two unambiguous cases: an equal-length calm corridor
   (isolating sea state as the only difference) and a severe band where avoidance really
   is optimal.

### Not done

- **Reliability is an input, not yet measured.** The kernel consumes a reliability factor;
  Phase 10.1 is what produces it from CRPS/Brier backtesting. Until then callers pass the
  registry's declared prior, which is a prior and is labelled as one.
- **Thresholds are engineering defaults, not regulation.** Only the FRP/trawler rows trace
  to METHODS.md; the catamaran and gillnetter rows are reasoned from seakeeping and should
  be reviewed with NIOT or a fisheries officer before the finale.
- **The zone `catch_signal` is a ranking signal, not a catch prediction**, and nothing
  downstream should present it as one.
- Kernels are not yet wired to live evidence — that is Phase 5's orchestration work.

### Next phase

**Phase 5 — agentic orchestration.** LangGraph state machine, the agent roster, planner
with a cost budget, multi-turn conversation state. This is where the kernels get their
inputs from the Phase 3 evidence layer, and where the first LLM in the system appears —
strictly outside the numeric path.

---

## Phase 5 — Agentic orchestration (2026-09-19)

### LangGraph is the engine; the fallback is real, not notional

LangGraph 1.2.11 installs and runs on this Python — typed state, fan-out and streaming
all verified. It is the engine. The documented FastAPI `DagEngine` fallback is also
built, and `TestEngineParity` asserts both produce the same result, because the node
functions are **pure and engine-agnostic**. That is the only honest way to offer a
fallback: two implementations that drift are not a fallback, they are a second system.

### The first LLM enters, outside the numeric path

A planner LLM may *propose* a DAG. It is parsed into `PlanSpec`, which validates node
names, tool allow-lists, dependencies and cycles on construction — an invalid plan cannot
exist as an object. **Any** failure (malformed JSON, unknown node, forbidden tool, extra
field, cycle) falls back to the deterministic `RulePlanner` and records the rejection.
The rule planner is the *default*, not the fallback: for the five canonical intents the
right DAG is known, and it means ORCA plans correctly with no API key at all.

### Delivered

- [x] **5.1 typed stateful graph** — LangGraph `StateGraph` plus the `DagEngine` fallback.
- [x] **5.2 the full roster** — planner, marine data, weather, geospatial, ecosystem,
      risk, route, reliability, verifier, response.
- [x] **5.3 validated DAG + bounded budgets** — tokens, wall-clock and API calls, enforced
      twice: **pruning** before execution (optional steps dropped, recorded with reasons)
      and a **ledger** during it (skips further work once a ceiling is hit). Pruning alone
      is optimistic; the ledger alone is too late.
- [x] **5.4 parallel fan-out + tool allow-lists** — execution layers derived from the DAG;
      each agent may call only its registered tools, enforced at plan validation *and* at
      call time. A forbidden call raises `PermissionError`, deliberately **not**
      `ToolFailure`: reaching outside an allow-list is our defect, not an upstream outage,
      and must not be silently replanned around.
- [x] **5.5 failure-aware replanning** — substitutions are **declared in advance**
      (`APPROVED_SUBSTITUTIONS`), so a "dynamic replan" cannot invent a step nobody
      approved. Safety-critical nodes have no substitute: when the safety kernel fails the
      run abstains rather than routing around it. Replans are bounded.
- [x] **5.6 multi-turn state + entity carry-over** — every slot records whether it came
      from this turn, an earlier one, or the profile. A **time window is never carried**:
      "and tomorrow?" changes the time and keeps the rest, so reusing yesterday's window
      would answer the wrong day.
- [x] **5.7 session memory** — vessel, home port, language, alert subscriptions.
- [x] **5.8 streamable events** — plan ready, pruned, node started/finished, evidence
      arrived, replanned, budget exhausted, run finished.

### Evidence

| Check | Result |
|---|---|
| `pnpm verify` | **exit 0** — ruff clean, `mypy --strict` clean (53 files), `tsc` clean |
| Unit tests | **309 passed** (was 269); 40 new in `services/orchestrator` |
| `pytest -m stack` | **53 passed** |

The three tests the phase named are all present and passing:
`test_and_tomorrow_carries_location_and_vessel`, `TestBudgetPruning` (including that the
engine emits a pruning event and the ledger halts work), and `TestFailureReplanning`
(marine-data failure replans onto the approved substitute; a safety-kernel failure
abstains instead).

### Two bugs the tests caught

1. **LangGraph fan-out was broken.** Plain `dict` state raised `InvalidUpdateError` the
   moment the DAG fanned out — which is every real ORCA plan. Fixed with an
   `Annotated[list[str], operator.add]` reducer. A second defect surfaced behind it: the
   state TypedDict was defined inside a function, so `get_type_hints` could not resolve
   `Annotated` under this module's postponed annotations. Moved to module level.
2. **A pruning assertion was stronger than the design.** It expected the plan to fit the
   budget after pruning, but only steps marked optional are pruned — when the remainder is
   all non-optional it deliberately runs anyway with the ledger as backstop, rather than
   dropping a step a kernel depends on. The test now asserts the estimate *falls* and
   points at the ledger test for the other half.

### Not done

- **No LLM has been called.** `LlmPlanner` is implemented and its schema validation is
  tested against hand-written payloads, but no API key exists here, so no real model has
  ever produced a plan. The rule planner is what runs.
- **Tools are injected, not wired.** The orchestrator is tested against stub tools; the
  adapters from Phases 1-4 are not yet connected to the node registry. That wiring is
  what makes the first end-to-end query possible and is the obvious next task.
- **Fan-out is parallel in dependency terms, not concurrent.** Nodes in a layer have their
  dependencies satisfied and could run concurrently; they currently run in sorted order
  because the bodies are synchronous kernel calls and determinism matters more for replay.
- Response synthesis is a stub: it assembles kernel results and provenance. Wording,
  translation and TTS are Phase 7.

### Next phase

**Phase 6 — the trust layer**: verifier/critique with fresh context, conflict resolution
with reliability-weighted source preference, evidence-sufficiency gating, and the
provenance graph with deterministic replay. Phase 3's `as_of` querying and Phase 4's
kernel fingerprints are the two pieces replay depends on, and both are in place.

---

## Phase 6 — Trust layer (2026-09-19)

### The rules decide; the critique only adds doubt

The ordering is the design. Seven **named** deterministic checks run first and set the
verdict. The critique runs afterwards, in a fresh context that is shown the evidence and
the check outcomes but **not** the reasoning that produced the answer — an adversarial
critic inheriting the generator's assumptions inherits its blind spots.

Its authority is asymmetric, and enforced in code rather than by convention: it **may**
add a caveat or escalate an answer to an abstention; it **cannot** clear a blocking check,
downgrade a severity, or turn an abstention into an answer. There is no code path back.
A critique that can argue the system out of a refusal will eventually do so on the one
query where the refusal was right.

`NullCritic` is the default, so ORCA is fully functional with no model configured.

### Delivered

- [x] **6.1 seven named checks** — `source_validity`, `freshness`, `spatial_consistency`,
      `missing_data`, `source_disagreement`, `formula_validity`, `evidence_sufficiency`.
      Each states its threshold and reports the observed value it compared, so a refusal
      is explainable by the number that caused it. Freshness applies **two** thresholds:
      the source's own cadence (catches a feed that stopped) and a 12 h absolute limit
      (catches a source whose cadence is simply too slow for the question asked).
- [x] **6.2 conflict resolution** — variable-specific thresholds (0.5 m for wave height,
      1.5 °C for SST, 45° for direction). The more reliable source wins, the uncertainty
      interval **widens to span all readings**, and the conflict is **disclosed as a
      caveat**. Averaging is deliberately not an option: the mean of a 1.2 m and a 2.4 m
      forecast is a number neither source predicted, belongs to no provenance, and cannot
      be replayed from either payload. Beyond 4× the threshold the disagreement is
      irreconcilable and blocks.
- [x] **6.3 abstention as a typed response** — a `TrustVerdict` with reasons, the full
      check list, and a **remedy** saying what would have to change. Not an exception:
      modelling it as one would push a deliberate refusal into a 5xx path where clients
      retry. The model rejects incoherent states (a blocking check with an `answer`
      status, an abstention with no reason).
- [x] **6.4 provenance graph + replay** — dataset → raw-payload hash → agent →
      formula+version → output, persisted per `run_id` (`0005_provenance.sql`). Edges may
      only run forward; a backwards edge is rejected because an output cannot influence
      its own inputs. The fingerprint **excludes wall-clock time**, since a replay records
      new timestamps and a fingerprint that changed because of that would be useless.
- [x] **6.5 legacy language retired** — "7-point integrity check" and "Adversarial
      Reliability Nucleus" are gone from `ARCHITECTURE.md` §1 and §4, replaced by the
      design as built. `CLAIMS.md` marks gap **G4 closed**. Remaining occurrences are
      deliberate historical "replaces X" framing in the gap analysis, which is the record
      of why the change was made.

### Replay is re-derivation, not re-running

The distinction is the whole value. Re-running a query tomorrow fetches a revised forecast
— a different answer proves nothing and an identical one proves nothing either. Replay
feeds the **archived bytes the original decision actually read** back through the same
formula versions, so any difference in output is a difference in **code**, not in the
weather. Three failure modes are reported distinctly rather than collapsed:

| Outcome | Meaning |
|---|---|
| `IDENTICAL` | fingerprints match; the decision reproduced exactly |
| `OUTPUT_DIFFERS` | same inputs, different result — names the output that moved |
| `FORMULA_CHANGED` | a version was bumped; a difference is **expected**, not tampering |
| `EVIDENCE_TAMPERED` | stored bytes no longer hash to the recorded digest |
| `EVIDENCE_UNAVAILABLE` | the archived payload is missing |

### Evidence

| Check | Result |
|---|---|
| `pnpm verify` | **exit 0** — ruff clean, `mypy --strict` clean (62 files), `tsc` clean |
| Unit tests | **361 passed** (was 309); 52 new in `services/trust` |
| `pytest -m stack` | **62 passed** (was 53); 9 new against real PostgreSQL |

`test_replay_returns_identical_output` is the Phase 6.4 assertion: a run re-derived from
its archived payload reproduces the original fingerprint exactly. `TestReplayFromTheDatabase`
does the same round trip through persistence — record, load, replay — which is what makes
the claim true months later rather than only within a process.

### Not done

- **No LLM critique has been run.** `LlmCritic` is implemented and its constraints are
  tested (transport failure swallowed, unexplained escalation dropped), but no API key
  exists here, so `NullCritic` is what executes. The verdict does not depend on it.
- **The trust layer is not yet wired into the orchestrator.** `services/trust` is a
  library with its own tests; the Phase 5 verifier node still uses its own simpler check.
  Connecting them is the obvious next task and belongs with the tool wiring.
- **Reliability is still an input.** Conflict resolution ranks by a reliability figure it
  is given; Phase 10.1 is what produces a backtested one. Until then it is the registry's
  prior, labelled as a prior.
- **`KNOWN_SOURCES` and the conflict thresholds are engineering judgements**, not sourced
  constants. Each is set near the point where a difference would change a decision, and
  they deserve review with a domain expert before the finale.
- The counterfactual "what-if" simulator (PLAN.md 10.3) is **not** part of Phase 6.

### Next phase

**Phase 7 — multilingual voice I/O**: ASR, IndicTrans2, and the TTS path that the Tamil
voice demo turns on.

## Git

Repository initialized 2026-09-18 (`git init -b main`); Phase 0 committed as
"Phase 0 foundation". `.env` is gitignored and no credential values exist in the tree —
`.env.example` ships with every secret blank. Generated contract artifacts
(`packages/schemas/generated/`) are committed deliberately: the web typecheck and build
import `types.ts`, so a fresh clone must not require a Python environment first.

---

## Done / claimed as done (per the PPT)

- [ ] *(verify)* Core reasoning pipeline "built and running within the first 12 hours" — PPT claim, not
      yet confirmed against a real codebase.
- [ ] *(verify)* Trust Layer and evidence verification "fully wired in by hour 24" — PPT claim.
- [ ] *(verify)* "Complete end-to-end demo tested and stage-ready by hour 36" — PPT claim.
- [x] Public site deployed at **https://orca-official.vercel.app/** (linked in the PPT).
- [x] Idea/architecture defined: layered pipeline (ingestion → orchestration → reasoning → evidence →
      trust → output), 5-agent mesh (Ocean, Fisheries, Ecosystem, Safety, Geo).
- [x] Domain outreach done: visited NIOT Pallikaranai, Chennai; spoke with Dr Aadhityan (Scientist A,
      Ocean Observation Systems) and Dr Nidhi Varshmey (Scientist F, Ocean Mining Department).
- [x] Research/reference list assembled (INCOIS, ISRO/MOSDAC, NASA, NOAA, Copernicus, one Springer paper).

> Mark each `[ ]` above as `[x]` once you've actually confirmed it against the running prototype —
> don't take the PPT's hour-12/24/36 claims as ground truth without checking.

## Pending — concrete gaps identified in the gap-closure report (G1–G11)

See `CLAIMS.md` for full detail on each. Checklist form for tracking:

- [ ] **G1** — Automatic language + code-mixed detection, voice-first interaction (Bhashini/AI4Bharat:
      IndicTrans2, IndicASR/IndicWav2Vec, IndicWhisper, Indic-Parler-TTS). PPT currently only lists
      multilingual support generically.
- [ ] **G2** — Explicit multi-turn conversation-state schema with entity carry-over
      (location/time/vessel) and clarifying-question behaviour.
- [ ] **G3** — Tool/dataset registry with capability metadata for autonomous dataset discovery (vs.
      hard-wired calls).
- [ ] **G4** — Concrete provenance graph (dataset → timestamp → agent → formula → output) +
      evidence-sufficiency gating with abstention, replacing the vague "7-point integrity check" /
      "Adversarial Reliability Nucleus" framing.
- [ ] **G5** — Scheduled, geofenced proactive alert-evaluation loop that *pushes* warnings (architecture
      is currently pull/query-centric only).
- [ ] **G6** — Concrete PostGIS distance-to-IMBL math with predictive drift warning (currently a USP
      slide with unspecified math).
- [ ] **G7** — Named route-optimization algorithm + cost surface (currently just a "Safe Route" output
      label).
- [ ] **G8** — Causal engine grounded in real oceanographic drivers (SST anomaly, chlorophyll, upwelling,
      marine heatwave, freshwater flux, overfishing) rather than a generic LLM guess.
- [ ] **G9** — Disaster-Management theme integration: IMD RSMC cyclone bulletins, INCOIS
      tsunami/storm-surge/high-wave/swell alerts, NDMA/SACHET, CAP XML standard, boat-recall/harbour-
      advisory/cyclone-shelter workflows.
- [ ] **G10** — Four persona views (fisherfolk, researcher, coastal authority/disaster agency, maritime
      operator) — currently fisherman-centric only.
- [ ] **G11** — A slide/demo beat for each SIH evaluation dimension (see `DEMO.md` §4).

## Pending — data ingestion (per `DEPLOYMENT.md`)

- [ ] INCOIS ERDDAP adapter (SST, winds, chlorophyll, ARGO) — highest priority, start here.
- [ ] IMD `api.imd.gov.in` adapter (sea bulletins, cyclone tracks, warnings).
- [ ] CMEMS adapter (waves — primary wave source; currents; chlorophyll fallback).
- [ ] Open-Meteo Marine fallback (fastest demo-safe fallback, no key).
- [ ] PostGIS load of EEZ (marineregions v12), agreed 1974/76 India–Sri Lanka IMBL geometry, WDPA MPA
      polygons.
- [ ] INCOIS PFZ text/WMS parser (no clean JSON API — needs scraping).
- [ ] MOSDAC `mdapi` client integration (optional/enhancement — registration + 3-day latency risk).
- [ ] NIOT OMNI buoy historical access for reliability backtesting (or synthetic-hindcast fallback).

## Pending — decision kernels (per `METHODS.md`)

- [ ] Marine Safety Score (vessel-class-normalized, reliability-weighted, hard-abstain on stale data).
- [ ] Fishing-Zone Intelligence (Pareto ranking, not single-point recommendation).
- [ ] Advisory Reliability Horizon (CRPS/Brier + conformal prediction, buoy-backtested) — **flagship
      differentiator, highest priority after basic ingestion.**
- [ ] Route optimization (isochrone-A* over a cost surface with geofence hard constraints).
- [ ] Causal fish-productivity engine (Granger-causality / constrained causal DAG).

## Pending — scientific corrections before the finale (per `METHODS.md` §3)

- [ ] Reframe PFZ as a probabilistic aggregation indicator (3×/week), not "where the fish are."
- [ ] Remove any claim of live/operational Oceansat-3 SST (SSTM non-operational) — use Oceansat-3 only for
      chlorophyll/winds.
- [ ] Either specify the training data + validation plan for the Random Forest/Gradient Boosting models,
      or reclassify them as deterministic scoring rules.
- [ ] State explicitly that satellite chlorophyll is gap-filled/composited, with visible data age.
- [ ] Confirm IMBL geometry uses the agreed 1974/76 boundary, not a computed EEZ median.

## Pending — evaluation artifacts (per `DEMO.md`)

- [ ] Golden query set (~30 queries).
- [ ] Hindcast backtest results (or documented synthetic-hindcast fallback).
- [ ] Ablation test results (with/without reliability layer, with/without verifier).
- [ ] Abstention/hallucination test, rehearsed as part of the live demo.
