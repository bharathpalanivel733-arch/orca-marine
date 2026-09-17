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
  Verification & Fusion Middleware — 7-point integrity check · cross-domain evidence fusion
    ★ Counterfactual Decision Simulator
    ★ Adversarial Reliability Nucleus (Confidence Decay Model)

DECISION & OUTPUT LAYER
  Inference & Decision Kernel — Weighted Feasibility Engine
  Natural-Language Synthesis Module
  Decision Output Endpoint
```

> **Note (from the gap-closure report):** "7-point integrity check" and "Adversarial Reliability Nucleus"
> are vague marketing terms a scientific jury will challenge. Replace with a concrete, named design before
> the finale — see `METHODS.md` §Provenance & Verifier for the recommended replacement
> (deterministic provenance graph + evidence-sufficiency gating with abstention).

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

## 4. Verification layer (from the PPT)

```
VERIFY  → 7-point check: source validity, timestamp freshness, spatial consistency,
          missing-data flag, agent disagreement, model validity, evidence sufficiency
FUSE & STRESS-TEST → combines evidence, tests recommendation against a counterfactual
                      decision simulator → advisory reliability horizon
GATE    → blocks recommendation if evidence insufficient → passes to Decision Engine
```
(As noted above, replace the "7-point check" framing with the concrete provenance/gating design in
`METHODS.md` before presenting to a technical jury.)

## 5. Recommended agent roster (gap-closure report — supersedes/extends the PPT's 5-agent mesh)

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
