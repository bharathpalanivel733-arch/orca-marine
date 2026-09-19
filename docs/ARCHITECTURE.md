# ARCHITECTURE.md — ORCA System Design

## 1. Layered architecture (as pitched in the PPT)

```
INGESTION LAYER
  Query Ingestion Interface — NL Query Parsing + Intent Capture

ORCHESTRATION LAYER
  Orchestration Microservice — Context Resolution + Dynamic DAG Completion

REASONING LAYER
  Multi-Agent Reasoning Mesh: Ocean | Fisheries | Ecosystem | Safety | Geo
    ★ Causal Hypothesis Engine
    ★ Constraint-Aware Reasoning Solver

  Evidence Aggregation Pipeline:
    Structured Telemetry (PostGIS / ML)
    Knowledge Corpus (RAG / pgvector)

TRUST LAYER
  Verification & Fusion Middleware — evidence-sufficiency gating · reliability-weighted
    conflict resolution · provenance graph with deterministic replay
    ★ Counterfactual Decision Simulator
    ★ Advisory Reliability Horizon (conformal intervals, backtested — Phase 10.1)

DECISION & OUTPUT LAYER
  Inference & Decision Kernel — Weighted Feasibility Engine
  Natural-Language Synthesis Module
  Decision Output Endpoint
```

> **RESOLVED (Phase 6, 2026-09-19).** The former "7-point integrity check" and "Adversarial
> Reliability Nucleus" phrasings were vague marketing terms a scientific jury would challenge, and
> they have been retired from this document. The trust layer as built is: seven **named**
> deterministic checks, each with a stated threshold and the observed value it compared; an
> independent critique that may only add doubt, never clear a check; reliability-weighted source
> conflict resolution that widens the uncertainty interval and discloses the disagreement; and a
> provenance graph (dataset → raw-payload hash → agent → formula+version → output) that a `run_id`
> replays byte-for-byte from archived evidence. See `services/trust/` and `METHODS.md`
> §Provenance & Verifier.

## 2. Technology stack (as pitched — technology choices, not yet all verified/built)

| Architecture layer | Technology | Why |
|---|---|---|
| Ingestion | Next.js, TypeScript, Tailwind, shadcn/ui | Fast, polished UI for real-time geospatial input |
| Orchestration | FastAPI, Pydantic | Async task routing + strict context validation before DAG execution |
| Reasoning | Xarray, GeoPandas, Random Forest, Gradient Boosting | Oceanographic grid processing + interpretable models across all 5 domain agents |
| Evidence Aggregation | PostgreSQL, PostGIS, pgvector | One database for structured evidence (PostGIS/ML) + knowledge corpus (RAG/pgvector) |
| Trust | Groq LLM, Agentic AI, RAG, Tool Calling | Fast inference for verification checks + document-grounded evidence fusion |
| Decision & Output | MapLibre GL, Shapely | Maps recommendations and route/zone constraints to final output |
| Deployment (cross-layer) | Docker, Google Cloud Run | Containerized services, auto-scale with regional query load |

**Recommended orchestration engine (gap-closure report):** LangGraph for the stateful agent graph
(checkpointing, memory, streaming, conditional branching, human-in-loop) — fits "dynamic DAG re-planning."
Keep deterministic tool execution in FastAPI microservices so runs stay reproducible/replayable. If
LangGraph slows the team down, a custom FastAPI DAG executor is an acceptable fallback.

## 3. Task orchestration (from the PPT)

```
UNDERSTAND & PLAN → Coordinator decomposes query into a task DAG
ROUTE & EXECUTE   → Only required agents activate, in parallel (Ocean, Fisheries, Ecosystem, Safety, Geo)
COLLECT           → Auditable results → feeds Master Architecture
```

## 4. Verification layer (as built — Phase 6)

```
VERIFY  → seven named deterministic checks, each with a stated threshold:
          source_validity · freshness · spatial_consistency · missing_data ·
          source_disagreement · formula_validity · evidence_sufficiency
          Each reports the observed value it compared, so a refusal is explainable
          by the number that caused it.

CRITIQUE → independent review in a fresh context. It may ADD a caveat or escalate to
          abstention; it may NOT clear a blocking check or turn an abstention into an
          answer. The rules decide; the critique explains.

FUSE    → source conflict resolved by backtested reliability for (region, variable,
          lead-time). The uncertainty interval WIDENS to span the disagreement and the
          conflict is disclosed to the user. Readings are never averaged: the mean of two
          forecasts is a number neither source predicted.

GATE    → abstain when evidence is stale, insufficient, or irreconcilably conflicting.
          Abstention is a typed response carrying reasons, the checks, and a remedy —
          not an error.

RECORD  → provenance graph per run_id: dataset → raw-payload hash → agent →
          formula+version → output. Replay re-derives from the archived bytes, so any
          difference in output is a difference in code, not in the weather.
```
Implemented in `services/trust/`. The old "7-point integrity check" and "Adversarial
Reliability Nucleus" phrasings are retired (PLAN.md 6.5).

## 5. Recommended agent roster (gap-closure report — supersedes/extends the PPT's 5-agent mesh)

> **Design intent, not as-built status.** The "Key tools" column names the service each agent is
> *designed* to call. As of Phase 8 no external provider has been called with credentials —
> no Bhashini key, no IMD key, no LLM API key, no push credentials. `PROGRESS.md` is the
> authoritative record of what is actually wired up, per phase, and section 4 above is the one
> part of this document written as-built.

| Agent | Responsibility | Key tools |
|---|---|---|
| Planner / Orchestrator | intent → DAG, tool selection, budget | LLM + tool/dataset registry |
| Marine Data | SST / chlorophyll / PFZ / currents retrieval | INCOIS ERDDAP, MOSDAC, CMEMS |
| Weather Intelligence | wind / waves / cyclone / lightning | IMD JSON, CMEMS waves, Open-Meteo |
| Geospatial / Geofence | IMBL / EEZ / MPA distance & containment | PostGIS |
| Ecosystem / Causal | productivity, HAB, causal DAG | time-series + Granger causality |
| Risk / Safety | boat-relative safety score | scoring kernel |
| Route | isochrone-A* over a cost surface | GeoPandas / NetworkX |
| Reliability | CRPS / Brier + conformal intervals | buoy hindcast store |
| Verifier / Critique | conflict + sufficiency gating | rules + LLM |
| Response Synthesizer | multilingual NL + provenance | Bhashini |

**Inter-agent message schema (suggested):**
```
{task_id, agent, status, inputs, evidence[], result, confidence, sources[], issued_time, cost_used}
```
on a shared, typed state object (Pydantic).

**Model-choice discipline (state this explicitly to the jury):**
- LLM only for: language detection, intent/entity extraction, evidence-fusion narrative, explanation
  synthesis, critique.
- Classical ML / statistics for: reliability scoring (CRPS/Brier/conformal), causal discovery
  (Granger/DAG), anomaly detection.
- Deterministic rules / PostGIS / graph search for: safety thresholds, geofencing, routing.
- **Never let the LLM invent a safety number — it only explains numbers the deterministic kernel computed.**

## 6. Request lifecycle (golden path, from the gap-closure report)

> Design intent. Steps 1 and 11 (ASR/TTS) are implemented in `services/speech` behind the
> documented fallback chains, but **no speech provider has been called live** — see PROGRESS.md.


1. Input (text or voice, any Indian language) → language detection + ASR (Bhashini/IndicWhisper) →
   normalize to a canonical intent request.
2. Intent + entity extraction (LLM, strict JSON schema): `{intent, location, time_window, objective,
   constraints, vessel_profile}`. Ask a clarifying question if a required slot is missing (multi-turn).
3. Context/memory resolution: vessel profile, home port, past queries, language preference.
4. Planner agent emits a dynamic task DAG with tool/agent selection under a cost budget (token +
   latency + API-call caps).
5. Parallel agent execution (fan-out); each agent calls only its registered tools.
6. Evidence retrieval: structured (PostGIS/TimescaleDB) + unstructured (pgvector RAG over advisory
   text, SOPs, bulletins).
7. Cross-agent evidence fusion + conflict resolution (e.g. INCOIS vs CMEMS wave disagreement → prefer
   the higher-reliability-scored source, widen uncertainty).
8. Verifier/critique agent + evidence-sufficiency gating: abstain if evidence is stale / insufficient /
   conflicting beyond a threshold.
9. Decision/scoring kernel computes Safety Score, Fishing-Zone Intelligence, Route, Reliability Horizon.
10. Explanation + provenance generation (which sources, ages, formulas).
11. Multilingual response synthesis (IndicTrans2 + Indic-Parler-TTS) + map/chart (MapLibre GL +
    Shapely/GeoPandas).
12. Proactive alert subsystem runs independently on a schedule, evaluating geofences/thresholds for
    at-sea users and pushing warnings.

## 7. Resilience design (from the PPT)

- Missing or conflicting data treated as normal operation, not failure.
- Confidence automatically downgrades; evidence is never fabricated.
- Recommendation is blocked when evidence is weak.
- Risk mitigation table:

| Condition | System response |
|---|---|
| Critical safety evidence missing | No strong recommendation |
| Conflicting evidence across agents | Surface the conflict |
| Stale or delayed forecast | Reduce reliability score |
| Official advisory exists | Authoritative warning takes precedence |
