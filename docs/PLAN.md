# PLAN.md — ORCA Build Plan (Phases & Sub-Tasks)

Team **OCEAN-IQ** · PS **SIH26176** · Theme: Disaster Management · Category: Software
Source of truth: `ORCA_FINAL.pdf` (Build, Gap-Closure & Differentiation Report, 8 Sep 2026) +
`SPEC.md`, `ARCHITECTURE.md`, `METHODS.md`, `CLAIMS.md`, `DEPLOYMENT.md`, `DEMO.md`, `PROGRESS.md`.

This file is the **execution plan**. Every phase below traces back to a clause (a)–(l) of the problem
statement, a gap G1–G11, or a Tier-1/Tier-2 differentiator. Nothing in the PDF is dropped; §0 lists the
few things that were in the PDF but **not** yet in `ARCHITECTURE.md` / `SPEC.md` and are now planned here.

---

## 0. Verification of existing docs against `ORCA_FINAL.pdf`

### 0.1 Already covered (no action needed)

| PDF section | Where it lives now |
|---|---|
| Clause checklist (a)–(l), stakeholders | `SPEC.md` §3, §4 |
| Gap table G1–G11 | `CLAIMS.md` §2 (verbatim coverage) |
| Scientifically loose claims (PFZ, Oceansat-3 SSTM, RF/GBM, chlorophyll, IMBL median) | `METHODS.md` §3 |
| Data ingestion mechanics (INCOIS ERDDAP IDs, IMD endpoints, MOSDAC `mdapi`, NIOT OMNI, CMEMS IDs, boundaries/MPA/AIS) | `DEPLOYMENT.md` §2 |
| Ingestion-realism table + resilient adapter design + graceful degradation | `DEPLOYMENT.md` §3–§5 |
| Tier-1 / Tier-2 differentiators, honest USP verdict, incumbents, "do not reinvent" | `CLAIMS.md` §3–§7 |
| Request lifecycle (12 steps), agent roster, message schema, LangGraph recommendation, model-choice discipline | `ARCHITECTURE.md` §5, §6 |
| Decision kernels (Safety Score, Pareto FZI, Reliability Horizon, isochrone-A*, PostGIS geofence, causal DAG) | `METHODS.md` §1 |
| Provenance graph + abstention replacing "7-point check" | `METHODS.md` §2, `ARCHITECTURE.md` §1 note |
| Evaluation strategy (golden set, hindcast, ablation, abstention) | `METHODS.md` §4, `DEMO.md` §3 |
| Demo script, jury-dimension mapping, positioning line | `DEMO.md` |
| Day-1 risk assumptions + change triggers | `DEPLOYMENT.md` §8 |

### 0.2 In the PDF but **missing / thin** in the current docs — now planned here

| # | Missing detail | PDF location | Planned in |
|---|---|---|---|
| M1 | **No LLM is actually chosen anywhere.** `ARCHITECTURE.md` says "Groq LLM"; the roster says "LLM + registry". No model IDs, no routing, no fallback, no cost budget. | "Model-choice discipline" | §1 (this file) |
| M2 | **Speech stack is one word.** ASR/TTS named only as "Bhashini/IndicWhisper", "Indic-Parler-TTS" — no pipeline, no voice cache, no quota fallback, no alert audio. | Lifecycle steps 1 & 11, risk (4) | §2 + Phase 7 |
| M3 | Alert **delivery mechanics**: CAP XML formatting, WebSocket/FCM push, GEMINI/DAT-SG hand-off documented as complement | "Proactive alert subsystem" | Phase 8 |
| M4 | **Cost budget on the planner** (token + latency + API-call caps) as a first-class agentic feature | Lifecycle step 4 | Phase 5.3 |
| M5 | **Embedding model / RAG corpus** for pgvector never specified (what gets embedded, which encoder, multilingual?) | Lifecycle step 6 | Phase 3.4 |
| M6 | **On-device small model** for offline cached-data Q&A never named | "Do NOT reinvent" / offline | §1.4, Phase 9.6 |
| M7 | **Day-by-day build sequence** (Days 1–2 / 3–5 / 6–8) exists in the PDF but in no doc | "Realistic build sequence" | §3 sequencing |
| M8 | **What-If Simulator** and **Mission Planner** appear as USP verdicts but have no design/owner | USP verdict (2) & (5) | Phase 10.3, 10.4 |
| M9 | **Participatory calibration loop** (Tier-2 #6) has no schema or storage plan | Tier 2 | Phase 10.5 |
| M10 | **Conflict-resolution rule** (INCOIS vs CMEMS wave disagreement → prefer higher reliability score, widen uncertainty) is stated but not specified as an algorithm | Lifecycle step 7 | Phase 6.2 |
| M11 | Inter-agent schema exists but **no persistence/replay store** for deterministic re-run | "Deterministic replay" | Phase 6.4 |

Action: after this plan is approved, fold M1/M2 into `ARCHITECTURE.md` §2 and §5, and M3 into
`DEPLOYMENT.md`, so the pitch docs and the build plan stay consistent.

---

## 1. LLM & model decisions (the M1 answer)

**Governing rule from the PDF, repeated on stage:** the LLM never invents a safety number. It does
language detection, intent/entity extraction, evidence-fusion narrative, explanation synthesis and
critique. Everything numeric comes from deterministic kernels, classical ML/statistics, PostGIS or graph
search.

### 1.1 Reasoning models (hosted)

| Role | Model | ID | Config | Why this one |
|---|---|---|---|---|
| **Planner / Orchestrator** — intent → task DAG, tool selection, re-planning | Claude Opus 5 | `claude-opus-5` | adaptive thinking, `output_config.effort: "high"`, `strict: true` tools, streaming | Hardest reasoning step in the system; DAG planning + tool selection under a budget is exactly long-horizon agentic work. Wrong plan = wrong everything downstream. |
| **Verifier / Critique agent** — conflict detection, evidence-sufficiency gating, adversarial critique | Claude Opus 5 | `claude-opus-5` | adaptive thinking, effort `high`, **fresh context** (does not see the planner's chain) | An adversarial critic must not inherit the generator's assumptions. Independent context + strongest model = the abstention decision the jury will probe. |
| **Explanation / evidence-fusion narrative** | Claude Sonnet 5 | `claude-sonnet-5` | adaptive thinking, effort `medium`, streaming | Writes prose over numbers the kernel already computed. Cheaper, 5× faster to first token, quality is sufficient for narration. |
| **Intent + entity extraction, language & code-mix detection, clarifying-question decision** | Claude Haiku 4.5 | `claude-haiku-4-5` | structured outputs (`output_config.format`), `max_tokens: 1024` | Tight JSON schema, sub-second, runs on every single turn including voice. |
| **Low-latency demo fallback path** | Groq-hosted open models | `llama-3.3-70b-versatile` / `gpt-oss-120b` | OpenAI-compatible endpoint behind our own `LLMClient` interface | Stage insurance: if network/API is degraded at the finale, one env var switches the narrative + extraction roles to Groq's very high tokens/sec. Declared openly as a fallback, not the primary. |

Implementation notes:
- All calls go through **one `LLMClient` abstraction** (`services/llm/client.py`) with `role` →
  `(provider, model, effort, schema)` resolved from a config file, so a model swap is a config edit.
- **Prompt caching** on the frozen system prompt + tool/dataset registry (stable prefix first, volatile
  query last) — cuts planner cost on multi-turn sessions.
- **Structured outputs / `strict: true`** everywhere the output feeds code (intent JSON, DAG spec,
  verifier verdict). Never regex-parse an LLM reply.
- Log `usage` (input/output/cache tokens) per task node into the provenance store → this is what powers
  the "cost budget" claim (M4) and the cost slide.

### 1.2 Non-LLM models (the part that makes the claim defensible)

| Job | Model / method | Notes |
|---|---|---|
| Translation (English ↔ 11+ Indian languages) | **IndicTrans2** (AI4Bharat, Apache-2.0), via Bhashini pipeline or self-hosted | Never translate numbers/units with an LLM — translate the rendered template. |
| Reliability scoring | CRPS, Brier, reliability diagrams, **conformal prediction** | `scipy` / `properscoring` / custom; no LLM. |
| Causal discovery | Granger causality + constrained causal DAG (PC / NOTEARS-style with domain edge constraints) | `statsmodels`, `causal-learn`. |
| Anomaly detection | SST-anomaly / chlorophyll-anomaly thresholds + isolation forest for HAB/marine-heatwave flags | Anchored to INCOIS ABIS semantics. |
| Safety / geofence / routing | Deterministic scoring rules, PostGIS, isochrone-A* | Never LLM. |
| RAG embeddings (M5) | **BGE-M3** (multilingual, 1024-d, self-hosted on CPU) into `pgvector` | Corpus: INCOIS PFZ text advisories, OSF bulletins, IMD sea-area/port/fishermen warnings, NDMA SOPs, state fishing-ban notifications, MPA rules. Multilingual encoder matters — queries arrive in Tamil. |

### 1.3 Speech models (the M2 answer — see Phase 7 for the build)

| Stage | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|
| **ASR** (voice → text, auto language ID) | **Bhashini ASR** (ULCA `/asr` pipeline, IndicConformer) | self-hosted **AI4Bharat IndicWhisper / IndicWav2Vec** (Apache-2.0) on the app server | browser Web Speech API (demo-only, English/Hindi) |
| **Language ID** | Bhashini pipeline `language-detection` task | Haiku 4.5 classification on the ASR transcript (handles code-mixed Tanglish/Hinglish) | user's stored language preference |
| **TTS** (text → speech, per language) | **Bhashini TTS** (ULCA `/tts`, IndicTTS voices, male/female) | self-hosted **Indic-Parler-TTS** (AI4Bharat/HF, Apache-2.0) | **pre-generated cached WAV/MP3** for all demo + alert strings |

TTS is **not optional and not cosmetic** — it is the delivery channel for a low-literacy, at-sea user, and
it is what the jury sees in the Tamil voice demo. Design points:
- TTS speaks only the **rendered answer template**, whose numbers come from the kernel — the same
  no-LLM-invents-a-number rule applies to audio.
- **Audio cache** keyed by `sha256(text) + lang + voice + speed` in object storage, so repeated advisories
  and all proactive alerts are instant and quota-free (directly de-risks PDF risk #4, Bhashini quotas).
- **Alert audio**: every CAP alert is rendered to speech at creation time in the user's language and
  pushed with the notification, so an at-sea user hears the cyclone warning without reading.
- Playback: chunked streaming in the browser (`MediaSource`), barge-in supported (stop audio on new mic
  input), plus a visible transcript for accessibility.
- Number/unit handling: expand "2.5 m" → spoken form per language before synthesis (a small normalizer
  table, not an LLM), otherwise Indic TTS mangles units.

### 1.4 Offline / on-device (M6)

- **Gemma 3 4B-IT** or **Llama 3.2 3B-Instruct**, 4-bit GGUF via `llama.cpp` / Ollama, answering **only**
  over the cached last-known advisory bundle, with a permanent "offline — data age N h" banner.
- No safety verdicts offline; the on-device model may read back cached numbers and hand off to
  GEMINI/DAT-SG for the truly offshore leg (stated explicitly, per the "do not reinvent" rule).

---

## 2. Phases

Each sub-task is written so it can be assigned and checked off. `→` marks the acceptance check.

### Phase 0 — Foundations & Day-1 risk validation
Closes: PDF "highest-risk assumptions", `DEPLOYMENT.md` §8.

0.1 Repo scaffold: `apps/web` (Next.js + TS + Tailwind + shadcn/ui), `services/api` (FastAPI + Pydantic
v2), `services/agents`, `services/ingest`, `packages/schemas` (shared Pydantic ↔ TS types), `infra`.
0.2 `docker-compose` dev stack: PostgreSQL 16 + **PostGIS** + **TimescaleDB** + **pgvector**, MinIO (object
cache), Redis (queue/cache). → `make dev` brings up a healthy stack.
0.3 Secrets/config: `.env.example` with INCOIS (none), IMD (none), CMEMS creds, MOSDAC SSO, Bhashini
ULCA key, Anthropic key, Groq key. Config-driven model routing file (§1.1).
0.4 **Day-1 risk spikes** (timeboxed, one owner each, results written back into `PROGRESS.md`):
  - (a) MOSDAC SSO registration + `mdapi` download; measure real latency. → go/no-go.
  - (b) Confirm INCOIS wave/OSF griddap ID live in the ERDDAP catalog. → else CMEMS `VHM0` is primary.
  - (c) NIOT OMNI buoy historical access for hindcast. → else synthetic-hindcast fallback declared now.
  - (d) Bhashini ULCA key + quota test on ASR and TTS. → else self-hosted Indic models + audio cache.
  - (e) Source the **agreed 1974/76 India–Sri Lanka IMBL geometry** (not a computed median). → hard blocker
    for Phase 2; do not ship a median line.
0.5 Observability from hour zero: structured JSON logs, OpenTelemetry traces per task-DAG node, a
`run_id` propagated end-to-end (this later *is* the replay key).

### Phase 1 — Ingestion layer (data adapters)
Closes: clause (d) spatio-temporal, G3; `DEPLOYMENT.md` §2–§4.

1.1 Adapter interface emitting the common record
`{variable, value, lat, lon, valid_time, source, issued_time, unit, quality, license}` + a uniform
`fetch(bbox, time_window, variables)` signature and per-source cadence metadata.
1.2 **INCOIS ERDDAP adapter** (griddap + tabledap): `incois_tmi_3day_datasets`,
`NOAA_AVHRR_AMSR_datasets`, `ascat_daily_datasets` / `incois_quickscat_daily_datasets` / `ascat_mnt_datasets`,
`incois_oceansat2_datasets`, `Indian_ARGO_Floats`. Verify dims via `.das` before hardcoding index order.
1.3 **IMD adapter** (`api.imd.gov.in/api/v1/…`): `seabulletin`, `coastalbulletin`, `portwarning`,
`fishermen-warning`, `cyclone_track`, `cyclone_wind` (GeoJSON wind radii 27/34/50/64 kt), `cyclone_cou`
(cone of uncertainty), `current_wx`, `districtnowcast`, `stationnowcast`, `districtwarning`. Honour IMD's
attribution + client-caching request.
1.4 **CMEMS adapter** (`copernicusmarine` toolbox): waves
`cmems_mod_glo_wav_anfc_0.083deg_PT3H-i` (VHM0/VTPK/VMDR + swell) as the **primary wave source**;
currents `cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m`; chlorophyll `cmems_mod_glo_bgc-pft_anfc_0.25deg_P1D-m`.
Run `copernicusmarine describe` in CI weekly — IDs rotate ~biannually.
1.5 **Open-Meteo Marine adapter** — keyless demo-safe fallback.
1.6 **INCOIS PFZ parser** — text advisories (`TextDataHome`) + WebGIS/WMS layers; normalize per coastal
node (~1,223 nodes); store `issued_time` and advisory age as first-class fields.
1.7 **INCOIS OSF (INDOFOS) mirror** — Hs, swell, currents, SST, MLD, D20, tides, wind at 3-h steps,
5–10-day horizon; scrape/mirror bulletins or substitute CMEMS equivalents where unavailable.
1.8 **MOSDAC adapter** (enhancement, gated on 0.4a): `mdapi` client, OCM-3 chlorophyll (`E06OCM_L2C_AD`),
SCAT-3 winds. **No Oceansat-3 SST anywhere in code, UI or slides** (SSTM non-operational).
1.9 **NIOT OMNI buoy ingest** (hourly obs) into the hindcast store — ground truth for Phase 4.
1.10 Storage: `xarray` + `dask` subsetting → Zarr / COG in MinIO; per-coastal-node time series into
TimescaleDB hypertables; raw payload archived for replay.
1.11 **Graceful degradation chain**, implemented as a policy not an if-else pile:
INCOIS SST → `NOAA_AVHRR_AMSR` → CMEMS `thetao`; MOSDAC chl → CMEMS BGC `chl`;
INCOIS waves → CMEMS `VHM0` → Open-Meteo. Every fallback recorded in provenance.
1.12 Cadence-aware cache (PFZ 3×/week, OSF 12 h, IMD bulletins hourly-ish, CMEMS waves 12 h) + staleness
flags. → A unit test proves a stale record is marked stale and never silently served.

### Phase 2 — Geospatial & geofencing
Closes: clause (i), G6, Tier-1 #3.

2.1 PostGIS load: EEZ (marineregions v12), **agreed 1974/76 India–Sri Lanka IMBL**, WDPA/Protected Planet
MPA polygons, state fishing-ban zones, OSM coastline, GEBCO bathymetry raster.
2.2 Containment/distance API: `ST_Contains`/`ST_Intersects` for restricted-zone checks; `ST_Distance` on
`geography` for true metric distance-to-IMBL.
2.3 Graded warning bands via `ST_Buffer`: 5 km **amber**, 2 km **red** (configurable per boundary type).
2.4 **Predictive drift warning**: project position forward from heading + speed + forecast surface current
over 15/30/60 min; raise the alert *before* the crossing; return time-to-boundary, not just distance.
2.5 Sustainability layers as hard constraints: MPA no-take, spawning/juvenile seasons, state ban periods
(Tier-2 #9) — modelled as polygons + date ranges so the solver can treat them as constraints.
2.6 Test set: known coordinates on both sides of the IMBL in the Palk Strait with expected
distance/verdict. → Regression test locks the boundary geometry.

### Phase 3 — Evidence layer (structured + RAG)
Closes: clause (e), G3, G4.

3.1 **Tool/dataset registry** with capability metadata (`variable`, `coverage_bbox`, `cadence`,
`latency`, `authority_rank`, `reliability_prior`, `cost`, `license`) — the planner selects sources from
this, never hardcoded calls. This *is* "autonomous dataset discovery" (G3).
3.2 Structured evidence store: TimescaleDB + PostGIS query layer with `as_of` semantics.
3.3 Knowledge corpus ingest (M5): PFZ advisories, OSF bulletins, IMD warnings, NDMA/SACHET SOPs,
fishing-ban notifications, MPA rules, INCOIS ABIS bulletins → chunk + embed with **BGE-M3** → `pgvector`.
3.4 Hybrid retrieval: BM25 (`tsvector`) + vector, reciprocal-rank fusion, with **hard filters on
`issued_time` and region** so a 2023 advisory can't answer a 2026 question.
3.5 Every retrieved chunk carries `{source, url, issued_time, age, authority_rank}` into the evidence bus.

### Phase 4 — Decision kernels (deterministic)
Closes: clauses (d)(e)(j), Tier-1 #1 & #2, `METHODS.md` §1.

4.1 **Vessel profile model**: class (FRP vallam / catamaran / mechanized trawler / gillnetter), LOA,
engine power, range, freeboard, crew, safety equipment → stored in agent memory per user.
4.2 **Marine Safety Score (0–100 + confidence band)**: weighted VHM0, wind speed, swell period,
squall/lightning risk, each normalized against **vessel-class thresholds** (e.g. FRP: Hs < 1.5 m caution,
< 2 m avoid; trawler higher), multiplied by the reliability factor for that region/lead-time.
**Hard-abstain if any driver is stale beyond its cadence.** → Unit tests per boat class.
4.3 **Fishing-Zone Intelligence**: Pareto rank over `{PFZ proximity & age, SST-front strength,
chlorophyll gradient, distance/fuel cost, safety score, IMBL/MPA/ban compliance}` → return the
**trade-off frontier** (2–3 non-dominated options), never one point (Tier-2 #7).
4.4 **Route optimization**: isochrone-A* over a gridded cost surface,
`cell_cost = f(VHM0, current vector projected on heading, wind, depth)`, with geofence polygons removing
or penalizing cells; cite Sen & Padhy 2015 (Applied Ocean Research) as North-Indian-Ocean precedent.
→ Benchmark vs naive great-circle on fuel/time/risk.
4.5 **Anomaly module** (Tier-2 #8): HAB/Noctiluca, oil-spill and marine-heatwave flags from ocean-colour +
SST anomalies, tied to INCOIS ABIS framing.
4.6 Kernel contract: every kernel returns `{value, inputs[], formula_id, formula_version, staleness,
abstain_reason?}` so provenance is automatic, not bolted on.

### Phase 5 — Agentic orchestration
Closes: clauses (a)(b)(c)(g), G2, M4, Tier-2 #10.

5.1 **LangGraph** stateful graph over a typed Pydantic state object; deterministic tool execution stays in
FastAPI microservices (reproducible/replayable). Fallback: custom FastAPI DAG executor if LangGraph churns.
5.2 Agent roster implemented as graph nodes: Planner/Orchestrator, Marine Data, Weather Intelligence,
Geospatial/Geofence, Ecosystem/Causal, Risk/Safety, Route, Reliability, Verifier/Critique, Response
Synthesizer.
5.3 **Planner with a cost budget** (M4): token + latency + API-call caps per query; the plan is a DAG spec
validated against the tool registry; over-budget plans are pruned before execution and the pruning is
shown in the trace.
5.4 Parallel fan-out execution with per-agent tool allow-lists; inter-agent message
`{task_id, agent, status, inputs, evidence[], result, confidence, sources[], issued_time, cost_used}`.
5.5 **Dynamic DAG re-planning**: on tool failure, staleness or a verifier bounce, the planner re-plans
(e.g. swap INCOIS→CMEMS, widen the time window, or escalate to a clarifying question).
5.6 **Multi-turn conversation state** (G2): explicit schema with entity carry-over
(location / time_window / vessel / objective / language), plus clarifying-question behaviour when a
required slot is missing. → Test: "and tomorrow?" resolves location + vessel from context.
5.7 Memory: vessel profile, home port, language preference, recent queries, per-user alert subscriptions.
5.8 Streaming the DAG to the UI (node started / evidence arrived / node done) — the visible "agents at
work" moment in the demo.

### Phase 6 — Trust layer (verification, fusion, provenance, replay)
Closes: clause (e), G4, Tier-1 #5, M10, M11.

6.1 **Verifier/critique agent** (Claude Opus 5, fresh context) checking: source validity, timestamp
freshness vs cadence, spatial consistency, missing-data flags, cross-agent disagreement, model validity,
evidence sufficiency — implemented as **deterministic rules first, LLM critique second** (the rules decide;
the LLM explains and catches what rules miss).
6.2 **Conflict resolution algorithm** (M10): when two sources disagree beyond a variable-specific
threshold (e.g. INCOIS vs CMEMS Hs > 0.5 m), prefer the source with the higher backtested reliability
score for that (region, variable, lead-time), **widen the uncertainty interval**, and surface the conflict
to the user rather than hiding it.
6.3 **Evidence-sufficiency gating with abstention**: if evidence is stale / insufficient / conflicting
beyond threshold → refuse with a reason ("I can't safely answer; nearest reliable data is 6 h old").
Abstention is a first-class response type in the API schema, not an error.
6.4 **Provenance graph + deterministic replay** (M11): every node persists
`dataset → timestamp → agent → formula(+version) → output` plus the raw payload hash; a `run_id` re-runs
byte-identically from the archived payloads. → Replay test asserts identical outputs.
6.5 Retire the phrases "7-point integrity check" and "Adversarial Reliability Nucleus" from all docs,
slides and UI; replace with "provenance graph + evidence-sufficiency gating."

### Phase 7 — Multilingual voice I/O (ASR · MT · **TTS**)
Closes: clause (f), G1, demo UX. Detail for M2.

7.1 **Audio capture** in the PWA: mic permission, VAD-trimmed 16 kHz mono WAV/Opus, 15 s cap, retry UI,
works on low-end Android Chrome.
7.2 **ASR service** (`/api/v1/speech/transcribe`): Bhashini ULCA ASR primary → self-hosted IndicWhisper
fallback → browser API last resort; returns `{text, lang, confidence, alternatives[]}`.
7.3 **Language + code-mix detection**: Bhashini language-detection, with Haiku 4.5 arbitration on
code-mixed input (Tanglish/Hinglish); result pinned to the conversation state and reused for the reply.
7.4 **Query normalization**: transcript → canonical intent request (Haiku 4.5, strict JSON schema),
including local place-name resolution (harbour/village gazetteer → lat/lon) — a big real-world failure
mode for Indic ASR output.
7.5 **Response templating**: kernel numbers → language-specific answer templates (safety verdict, zone
recommendation, geofence warning, abstention). Templates are translated with **IndicTrans2**, not
free-generated, so numbers and units survive.
7.6 **TTS service** (`/api/v1/speech/synthesize`): Bhashini TTS primary → self-hosted **Indic-Parler-TTS**
fallback → pre-generated cache; per-language voice selection (gender/speed configurable); number/unit
normalizer before synthesis.
7.7 **Audio cache + pre-generation**: cache key `sha256(text)+lang+voice+speed` in MinIO; a build-time job
pre-generates every demo string and every standard alert phrase in all supported languages.
→ Demo works even with Bhashini fully down.
7.8 **Playback UX**: streaming playback, play/pause, replay, barge-in (mic input cancels audio), visible
transcript, and a "read aloud" button on every recommendation and alert card.
7.9 **Alert TTS**: each CAP alert is synthesized at creation in the recipient's language and attached to
the push payload (see Phase 8) — the at-sea user hears it.
7.10 Language scope for the finale: **Tamil, Hindi, English** fully rehearsed; Telugu, Malayalam, Odia,
Bengali, Gujarati, Marathi, Kannada enabled and smoke-tested. → Matches/beats SAMUDRA's 10-language menu,
but auto-detected and voice-first (that's the G1 differentiator, say it on stage).

### Phase 8 — Proactive alert subsystem (push, not pull)
Closes: clauses (h)(l), G5, G9, M3.

8.1 Scheduled worker (Celery beat / Cloud Scheduler) evaluating, per active at-sea user:
(a) IMD `cyclone_wind` / `cyclone_cou` GeoJSON containment, (b) high-wave / lightning / squall thresholds
against the vessel profile, (c) geofence proximity + predictive drift.
8.2 **CAP (Common Alerting Protocol) XML** emission — interoperable with NDMA/SACHET conventions;
severity/urgency/certainty mapped from the kernel, not from an LLM.
8.3 Delivery: in-app WebSocket + Web Push/FCM, with the TTS audio attached (7.9); deduplication and
escalation rules so a user isn't spammed on every tick.
8.4 Disaster-management workflows: **boat recall**, **harbour advisory**, **cyclone-shelter guidance**,
plus an authority-side broadcast to a selected region/fleet.
8.5 **Explicit hand-off documentation**: for the truly offshore leg, ORCA defers to GEMINI (GAGAN/NavIC),
DAT-SG and Sagarmitra — shown in the UI and said on stage; ORCA does not claim a new offshore channel.
8.6 Alert audit: every alert stored with its provenance graph and the thresholds that fired it.

### Phase 9 — Interfaces & persona views
Closes: clause-level stakeholder coverage, G10.

9.1 **Fisherfolk PWA** (voice-first): big mic button, safety verdict card with confidence band, Pareto
zone cards on a MapLibre GL map, geofence banner with distance + time-to-boundary, data-age badges,
"read aloud" everywhere.
9.2 **Researcher view**: raw time series, causal DAG viewer, provenance graph explorer, CSV/NetCDF export
with citation block.
9.3 **Coastal-authority / disaster-agency view**: aggregate risk heat map, fleet-at-sea count, alert
dissemination console, CAP feed.
9.4 **Maritime-operator view**: port warnings, sea-area bulletins, route ETAs under forecast conditions.
9.5 **Provenance panel** (shared component): per-source name, timestamp, age, formula version, reliability
score — this is the trust moment in the demo.
9.6 **Offline/degraded mode** (M6): cached advisory bundle + on-device small model, permanent data-age
banner, no safety verdicts offline, explicit GEMINI/DAT-SG hand-off card.
9.7 Accessibility: large touch targets, high-contrast at-sea theme, works one-handed, audio-first paths
for every critical action.

### Phase 10 — Reliability, causality and the "what-if" family
Closes: Tier-1 #1 & #4, USP verdicts (2) & (5), Tier-2 #6, M8, M9.

10.1 **Advisory Reliability Horizon (flagship — most engineering time goes here)**:
  - Build the hindcast store: paired (forecast, OMNI-buoy observation) by region × variable × lead-time.
  - Compute **CRPS**, **Brier**, reliability diagrams per bucket.
  - Wrap forecasts in **conformal prediction** for distribution-free interval coverage.
  - Expose a **confidence-decay curve** in the UI and a spoken sentence: "at 24 h lead this forecast has
    been within ±0.4 m 90% of the time; at 5-day lead, 60%."
  - **Trigger (from the PDF):** if buoy hindcast data isn't assembled by Day 4, ship it as a rigorous
    design with a **synthetic hindcast** — do not drop the concept.
10.2 **Causal fish-productivity engine**: co-located time series (SST anomaly, chlorophyll trend,
upwelling index, marine-heatwave days, freshwater flux, fishing effort from Global Fishing Watch) →
Granger / constrained causal DAG → **evidence-ranked hypothesis set**, grounded in Arabian-Sea literature
(warming → stratification → diatom→Noctiluca shift → altered productivity).
10.3 **What-If simulator (scoped honestly, M8)**: forecast-driven **scenario replay** only — "leave 3 h
later", "go 10 km further", "wait a day" — re-running the kernels over the existing forecast fields.
Explicitly **not** a physics ocean model; say so on stage.
10.4 **Mission Planner (M8)**: goal + constraints → departure window, fishing window, return window,
solved as a constraint problem over safety, fuel/range, geofence and sustainability constraints, ranked on
the Pareto frontier from 4.3.
10.5 **Participatory calibration loop (M9)**: fisher-reported actual catch / observed sea state →
`observations` table → local recalibration of PFZ confidence and safety thresholds (lightweight active
learning), with the recalibration visible in provenance.

### Phase 11 — Evaluation, rigor & jury artifacts
Closes: G11, `METHODS.md` §4, `DEMO.md` §3.

11.1 **Golden query set (~30)** across the five canonical intents (safety, fishing zone, route, causal/why,
geofence) with documented expected behaviours, incl. multilingual and voice variants.
11.2 **Hindcast backtest report** vs OMNI buoys (or the documented synthetic fallback) with actual numbers.
11.3 **Ablation study**: with/without the reliability layer, with/without the verifier → a table showing
each component's contribution.
11.4 **Abstention / hallucination test**: stale and missing data injected → system must refuse and explain.
Automated, and rehearsed live.
11.5 Latency & cost budget report per query type (tokens, API calls, wall-clock) — feasibility evidence.
11.6 **Scientific-claims audit** before the finale, enforcing `METHODS.md` §3: PFZ framed as a
probability-of-aggregation indicator issued 3×/week; no operational Oceansat-3 SST anywhere; RF/GBM either
given a labelled dataset + hindcast validation or reclassified as deterministic scoring rules; chlorophyll
described as gap-filled/composited with visible age; IMBL from the agreed 1974/76 geometry.
11.7 One slide/demo beat per SIH evaluation dimension (novelty, complexity, clarity, feasibility,
practicability, sustainability, scale of impact, UX, future potential + technical feasibility, research
quality, team dynamics) per `DEMO.md` §4.

### Phase 12 — Deployment & operations
Closes: `DEPLOYMENT.md` §1, §6, §7.

12.1 Containerize every service; Google Cloud Run for API/agents/workers; managed Postgres with PostGIS +
TimescaleDB + pgvector; MinIO/GCS for Zarr/COG/audio cache; Next.js frontend on Vercel
(`orca-official.vercel.app` already live).
12.2 CI: lint, type-check, unit + geofence regression + replay determinism tests, weekly
`copernicusmarine describe` drift check, nightly golden-query smoke run.
12.3 Nightly precompute of coastal-node time series + map tiles; memoized agent results keyed by
`(intent, location-cell, valid-hour)`.
12.4 Rate limiting, IMD attribution + client caching compliance, source licence registry surfaced in the
provenance panel.
12.5 Cost control: cached evidence + selective agent activation + prompt caching; publish the per-query
cost number on the feasibility slide.
12.6 Scalability roadmap on one architecture: prototype → regional pilot → operational scale → national
layer.

### Phase 13 — Demo, narrative & rehearsal
Closes: `DEMO.md`.

13.1 Rehearse the golden path: Tamil **voice** query → DAG fan-out → Safety Score 72/100 with decaying
confidence → "4 km from the Sri Lankan maritime boundary, stay west" → Pareto zone trade-off → provenance
panel → **spoken Tamil answer**.
13.2 Rehearse the **proactive cyclone alert** push (with alert audio).
13.3 Rehearse the **researcher view** on "why has productivity declined here?" (causal DAG).
13.4 Rehearse **deterministic replay** of the whole decision.
13.5 Rehearse the **abstention demo** on stale data — deliberately, as a differentiator.
13.6 Lock the opening line: *"ORCA is the agentic reasoning and trust layer on top of INCOIS, IMD and
ISRO's existing marine data — not a new data source or a competing app."*
13.7 Offline demo kit: recorded audio, cached API responses, local DB snapshot — the demo must survive a
dead venue network.

---

## 3. Sequencing (maps the PDF's Days 1–2 / 3–5 / 6–8 onto the phases)

| Window | Phases | Exit criterion |
|---|---|---|
| **Days 1–2** | 0, 1.1–1.5, 2.1–2.3, one vertical slice of 5 | One golden query end-to-end on live INCOIS + IMD + CMEMS + Open-Meteo, EEZ/IMBL/MPA loaded in PostGIS |
| **Days 3–5** | 4.1–4.4, 2.4, 5 (full DAG, 5+ agents), 8.1–8.3, 9.1 | Boat-relative Safety Score, Pareto zones, distance-to-IMBL with drift, proactive alert loop, MapLibre UI |
| **Days 6–8** | 10.1, 10.2, 7 (voice + **TTS**), 6.4–6.5, 6.3, 9.2–9.4, 11, 13 | Reliability horizon (buoy or synthetic hindcast), causal engine, Tamil voice in/out, provenance & replay panel, verifier/abstention, persona views, demo polish |

**Priority rule from the PDF, if time is short:** build the Reliability Horizon, boat-relative risk and
the IMBL predictive geofence *before* polishing the chat UI. Those three are the non-duplicative core.

---

## 4. Traceability matrix

| Clause / gap | Phase |
|---|---|
| (a) agentic planning + decomposition | 5.1, 5.3, 5.5 |
| (b) multi-agent collaboration | 5.2, 5.4 |
| (c) tool selection | 3.1, 5.3 |
| (d) spatio-temporal reasoning | 1, 2, 4 |
| (e) explainable / evidence-based | 3.5, 4.6, 6, 9.5 |
| (f) multilingual regional languages | 7 (incl. **TTS** 7.6–7.10) |
| (g) multi-turn + refinement | 5.6, 7.3 |
| (h) proactive safety alerts | 8 |
| (i) geofencing IMBL/MPA | 2 |
| (j) route optimization | 4.4 |
| (k) causal questions | 10.2 |
| (l) disaster-management theme | 8.2, 8.4, 9.3 |
| Stakeholder coverage | 9.1–9.4 |
| G1 | 7 · G2 → 5.6 · G3 → 3.1 · G4 → 6 · G5 → 8 · G6 → 2.4 · G7 → 4.4 · G8 → 10.2 · G9 → 8.2/8.4 · G10 → 9.2–9.4 · G11 → 11.7 |
| Tier-1: reliability / boat-relative risk / IMBL drift / causal / replay | 10.1 · 4.1–4.2 · 2.4 · 10.2 · 6.4 |
| Tier-2: calibration / Pareto / anomaly / sustainability / agentic innovations | 10.5 · 4.3 · 4.5 · 2.5 · 5.3+5.5+6.3 |

---

## 5. Standing risks & triggers (carried from the PDF)

| Risk | Trigger | Action |
|---|---|---|
| MOSDAC blocked / 3-day latency | Day 1 spike fails | Drop MOSDAC to "enhancement"; rely on CMEMS + INCOIS; **never mention Oceansat-3 SST** |
| INCOIS wave/OSF griddap ID unconfirmed | Day 1 spike fails | CMEMS `VHM0` is the primary wave source (already the plan) |
| OMNI buoy history unavailable | Not assembled by Day 4 | Ship Reliability Horizon as rigorous design + synthetic hindcast demo |
| Bhashini quota / outage | Load test fails | Self-hosted IndicWhisper + Indic-Parler-TTS + pre-generated audio cache (7.7) |
| IMBL geometry wrong | Any time | Hard stop — use the agreed 1974/76 boundary only; a median line mis-warns fishermen |
| LangGraph churn | Slowing the team by Day 3 | Fall back to the custom FastAPI DAG executor |
| Venue network dead | Demo day | Offline demo kit (13.7) |
