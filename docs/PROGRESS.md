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

### NOT verified — do not claim these

- **The Docker stack has never started — root cause identified 2026-09-18.** Docker
  Desktop was fully restarted (all processes killed, relaunched from
  `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`) and the engine still
  fails. Evidence:
  - `docker info` → times out (exit 124); `docker ps` → `500 Internal Server Error ...
    dockerDesktopLinuxEngine/v1.56/containers/json`
  - `pnpm db:up` → `500 Internal Server Error ... /_ping`; `pnpm db:verify` → stops at
    its first guard: `FAIL docker daemon is not responding`
  - Docker Desktop's own log: `apiproxy ... dialing 192.168.65.7:2376: context canceled`
    — the Linux VM is not running.
  - `wsl --list --verbose` → **"has no installed distributions"**;
    `wsl --status` → **"WSL2 is unable to start since virtualization is not enabled on
    this machine. Please ensure the 'Virtual Machine Platform' optional component is
    enabled and virtualization is turned on in your computer's firmware settings."**
  Docker Desktop here uses the WSL2 backend, so with no WSL distro and WSL2 unable to
  start, the Linux engine cannot come up. **Therefore PostGIS / TimescaleDB / pgvector
  remain UNVERIFIED**, and PLAN 0.2's acceptance criterion is still open. The
  `timescale/timescaledb-ha:pg16` image has never been pulled, so whether it carries
  pgvector is still an assumption.
- **No data source is live.** INCOIS, IMD, CMEMS, MOSDAC, NIOT, Bhashini and every LLM
  provider are unconfigured and uncontacted. No credentials exist in this environment.
- **0.4 risk spikes (a)–(e) were not run** — see blockers.

### Risks

- **Python 3.14 on the host vs 3.12 in containers.** The venv installs fine on the host's
  3.14 today, but the Phase 1 science stack (xarray, GeoPandas, Shapely, psycopg) has
  reliable wheels on 3.12, which is what `services/api/Dockerfile` pins. Expect host/
  container divergence at Phase 1; the container is authoritative.
- **`timescale/timescaledb-ha:pg16` is an assumption.** It is chosen because it bundles all
  three extensions, but that has not been confirmed on this machine. If the image does not
  carry pgvector, the fallback is `postgis/postgis:16-3.4` plus a build step installing
  TimescaleDB and pgvector.
- **Readiness probes are TCP-only.** They prove a port answers, not that the database is
  usable, and the payload says exactly that. Phase 1 should replace them with a real query
  once a driver is in the dependency set.
- `make` and `psql` are absent on this machine; pnpm scripts and `docker compose exec` are
  the working entrypoints. PLAN 0.2's literal "`make dev`" wording is satisfied by
  `pnpm dev`.

### Blockers

1. **WSL2 / virtualization is unavailable — blocks all Docker work.** A Docker Desktop
   restart was attempted on 2026-09-18 and did not fix it; this is not a Docker problem
   but a host-platform one, and it **cannot be resolved from this session**: the fix
   needs Administrator rights (this session runs unelevated — confirmed via
   `WindowsPrincipal.IsInRole`) and a reboot.
   Fix, in order, on the dev machine:
   1. In an **Administrator** terminal: `wsl --install --no-distribution` (enables the
      Virtual Machine Platform component), then **reboot**.
   2. If WSL still refuses after the reboot, enable virtualization (Intel VT-x) in the
      BIOS/UEFI — `Win32_Processor.VirtualizationFirmwareEnabled` currently reads
      `False`, though VBS is reported running, so the firmware setting should be checked
      only if step 1 does not resolve it.
   3. Then: `pnpm db:up && pnpm db:verify`.
   Fallback if the machine cannot be changed: run PostgreSQL 16 + PostGIS + TimescaleDB +
   pgvector on another host (or a managed instance) and point `DATABASE_URL` at it — the
   code has no local-Docker assumption.
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
