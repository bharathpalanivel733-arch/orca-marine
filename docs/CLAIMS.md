# CLAIMS.md — Gap Analysis, USPs & Differentiation

## 1. What the PPT already covers well

- The agentic orchestrator: intent → planning → decomposition → tool selection → agent coordination →
  memory.
- The specialized-agent roster (Ocean, Fisheries, Ecosystem, Safety, Geo).
- The dynamic task DAG.
- The layered ingestion → orchestration → reasoning → evidence → trust → output stack.

Per the gap-closure report, this is "a solid skeleton and better-articulated than most competing teams
will manage" — but several things the jury will specifically probe are currently under-specified.

## 2. Clause-by-clause gap analysis (G1–G11)

| # | Problem-statement clause | ORCA's current state (per PPT) | Gap to close |
|---|---|---|---|
| G1 | Multilingual auto-detection & regional-language response | Multilingual support listed generically | Incumbent SAMUDRA already offers menu-based language switching (English, Hindi, Gujarati, Marathi, Kannada, Malayalam, Tamil, Telugu, Odia, Bengali). ORCA must go beyond menu switching to **automatic language + code-mixed detection** and **voice-first interaction** for low-literacy fishermen. Use Bhashini / AI4Bharat (IndicTrans2 NMT, IndicASR/IndicWav2Vec, IndicWhisper, Indic-Parler-TTS — mostly Apache-2.0). **BUILT (Phase 7, 2026-09-19):** deterministic script-based language detection with code-mix measurement and romanized Tanglish/Hinglish handling, voice capture and playback in the PWA, ASR and TTS endpoints behind documented fallback chains, and pre-translated templates so no model ever writes a sentence or alters a number. **Caveat: no speech provider has been called** — Bhashini needs a ULCA key and the self-hosted models need weights, neither of which exists here; what is tested is the request shapes and the fallback behaviour. The Tamil and Hindi wordings are unreviewed IndicTrans2 drafts. G1 is closed in design and in code, not in live integration. |
| G2 | Multi-turn context & query refinement | "Memory" listed | Needs an explicit conversation-state schema, entity carry-over (location/time/vessel), and clarifying-question behaviour when a query is under-specified. |
| G3 | Autonomous dataset discovery | "Tool selection" listed | Needs a tool/dataset registry with capability metadata so the planner can choose the right source (e.g. prefer INCOIS SST, fall back to CMEMS) — not hard-wired calls. |
| G4 | Explainability & evidence provenance | "Explainable recommendation," "7-point integrity check" | The "7-point integrity check" and "Adversarial Reliability Nucleus" are vague marketing terms a scientific jury will challenge. Replace with a concrete provenance graph (dataset → timestamp → agent → formula → output) and evidence-sufficiency gating with abstention. |
| G5 | Proactive alerting vs. pull queries | Architecture is pull/query-centric | The statement explicitly requires proactive cyclone/lightning/high-wave alerts. Needs a scheduled, geofenced alert-evaluation loop that **pushes** warnings, not just answers questions. **BUILT (Phase 8, 2026-09-20):** `services/alerts/` evaluates every at-sea subscriber on a tick against cyclone containment, vessel-relative thresholds and predictive drift; deduplication protects the channel and escalation always overrides it. **Caveat: nothing has been delivered anywhere** — no FCM account, no VAPID key, no scheduler running the tick. G5 is closed in design and in code, not in live delivery. |
| G6 | Geofencing incl. IMBL & MPAs | Listed as a USP but math unspecified | Highest-empathy real problem — Sri Lankan Navy statements report 346 Indian fishermen apprehended and 44 trawlers seized in 2025, overwhelmingly for crossing the IMBL in the Palk Strait. Needs concrete PostGIS distance-to-IMBL with predictive drift warning. |
| G7 | Route optimization | "Safe Route" output | Needs a named algorithm and cost surface — see `METHODS.md`. |
| G8 | Causal "why productivity declined" | "Causal Hypothesis Engine" | Must be grounded in real oceanographic drivers (SST anomaly, chlorophyll, upwelling, marine heatwave, freshwater flux, overfishing) — not a generic LLM guess. |
| G9 | Disaster-Management theme | Weak | Integrate IMD RSMC cyclone bulletins, INCOIS tsunami/storm-surge/high-wave/swell alerts, NDMA/SACHET, and ideally the CAP (Common Alerting Protocol) XML standard. Add boat-recall / harbour-advisory / cyclone-shelter workflows. **BUILT (Phase 8, 2026-09-20):** CAP 1.2 emission with deterministic severity/urgency/certainty, plus boat recall, harbour advisory, cyclone-shelter guidance and authority broadcast, each attributed to the issuing authority rather than to ORCA. **Caveat: no real IMD cyclone GeoJSON has been ingested** — containment is tested against constructed polygons — and the shelter registry is three unverified sample entries. |
| G10 | Stakeholders beyond fishermen | Fisherman-centric | Add explicit personas: researcher view (raw data + provenance + export), coastal-authority/disaster-agency view (aggregate risk map, fleet-at-sea count, alert dissemination), maritime-operator view (port warnings, sea-area bulletins, route ETAs). Cheap to add, scores directly against the clause. |
| G11 | Evaluation/jury dimensions | Not addressed | Build a slide for each evaluation dimension (novelty, complexity, clarity, feasibility, practicability, sustainability, scale of impact, UX, future potential). |

## 3. Honest verdict on the 5 claimed USPs (from the PPT)

1. **Causal / Hypothesis Reasoning** — genuinely differentiating *if* grounded in real drivers +
   a DAG/Granger method; as a bare LLM prompt it is not. → Sharpen with method + data (see `METHODS.md`).
2. **What-If Marine Simulator** — appealing but easy to over-claim. Scope it to
   **forecast-driven scenario replay** ("if I leave 3 h later / go 10 km further"), not a physics ocean
   model. Keep, but scope honestly.
3. **Context-Aware Marine Geofencing** — strong and real; make the **IMBL predictive-drift** the hero
   feature. Keep and elevate.
4. **Advisory Reliability Horizon** — the best USP, currently under-built. Add conformal prediction +
   buoy backtesting. **Invest the most engineering time here.**
5. **Mission Planner** — good product framing (goal → constraint-aware plan with departure/fishing-
   window/return). Novel as an agentic constraint solver; connect it to the Pareto trade-off ranking and
   geofence/sustainability constraints.

## 4. Tier 1 differentiators — build these, they win the round

1. **Advisory Reliability Horizon, done rigorously** (flagship) — see `METHODS.md`. No Indian fisher
   system quantifies its own trust today.
2. **Boat-capability-relative risk framing** — personalize Safety Score by vessel class/power/range/
   freeboard, not a generic colour-coded sea-state band.
3. **IMBL-crossing prevention with predictive drift** — real-time distance-to-boundary + forward
   projection from heading/speed/current. Directly addresses the 2025 arrests. GEMINI warns of weather,
   not boundary proximity — this is a real gap.
4. **Causal fish-productivity engine** — constrained causal DAG / Granger-causality, evidence-ranked
   hypothesis set, grounded in documented Arabian-Sea science.
5. **Deterministic replay + provenance graph** — an auditable decision graph, replacing the vague
   "7-point integrity check." Strongest trust signal for a government safety-system jury.
   **BUILT (Phase 6, 2026-09-19):** `services/trust/` implements seven named deterministic checks,
   reliability-weighted conflict resolution that widens uncertainty and discloses the disagreement,
   abstention as a typed response, and a `run_id` provenance graph that replays byte-for-byte from
   archived payloads. Gap G4 is closed.

## 5. Tier 2 — strong additions if time allows

6. **Participatory calibration loop** — fishermen report actual catch/sea-state; system recalibrates PFZ
   confidence and safety thresholds locally (lightweight active learning).
7. **Multi-objective Pareto zone/route ranking** — trade-off frontier over catch-probability vs fuel vs
   risk vs IMBL/MPA compliance, not a single answer.
8. **Anomaly detection module** — HAB (Noctiluca), oil-spill, marine-heatwave flags from ocean-colour +
   SST anomalies, tied into INCOIS ABIS.
9. **Sustainability constraints as first-class** — juvenile/spawning seasons, state fishing-ban periods,
   MPA no-take zones, bycatch as hard constraints in the reasoning solver. Cheap, aligns directly with the
   "sustainability" scoring dimension.
10. **Agentic-AI innovations** — adversarial verifier/critique agent, tool-use planning with cost budgets,
    evidence-sufficiency gating with abstention, dynamic DAG re-planning. Make the "agentic" claim real,
    not cosmetic.

## 6. Do NOT reinvent — integrate instead

- Delay-tolerant / offline delivery already exists: GEMINI (GAGAN/NavIC), DAT-SG distress, Sagarmitra.
- SMS/WhatsApp/Telegram advisory reach already exists via INCOIS.
- Position ORCA's offline mode as **complementary**: cache last-known advisory + a small on-device model
  for cached-data Q&A, and explicitly hand off to GEMINI/DAT-SG for the truly offshore leg. Say this
  explicitly on stage.

**BUILT (Phase 8, 2026-09-20).** The hand-off is no longer only a talking point. It is data in
`services/alerts/orca_alerts/handoff.py`, rendered in the payload and asserted by tests, so the claim
cannot quietly drift into an overstatement. The sentence for the UI and the stage, verbatim:

> ORCA delivers over mobile data and does not provide an offshore channel. Beyond cellular coverage,
> ORCA defers to GEMINI (GAGAN/NavIC) for warnings, DAT-SG for distress alerting, and INCOIS Sagarmitra
> for advisory dissemination. ORCA complements these services and does not replace or compete with them.

Beyond ~10 km offshore the UI warns that alerts may not arrive; beyond ~20 km it states plainly that
ORCA cannot reach the vessel and names the services that can. The wording is blunt rather than hedged
on purpose — "coverage may be limited" reads as boilerplate and gets ignored, and a fisherman who
believes ORCA will warn him 60 km out is one who stops watching the sky.

## 7. Incumbents to differentiate against (name these on stage — shows domain awareness)

- **INCOIS SAMUDRA / SAMUDRA 2.0** — tsunami/high-wave/swell alerts, PFZ, 5-day OSF, tides, 10 languages
  (menu-based).
- **GEMINI** — GAGAN/NavIC deep-sea alerts + DAT-SG distress transmitter + Sagarmitra.
- **mKRISHI Fisheries** (TCS + CMFRI + INCOIS) and **Fisher Friend** (MSSRF).
- **Jal Anveshak** — research prototype, fine-tuned LLaMA-2 for PFZ navigation.

## 8. Scale-of-reach context (useful for the "impact" slide)

INCOIS already reaches large fisher populations directly — on the order of ~100k–160k users per coastal
state via WhatsApp/SMS/Telegram/SAMUDRA (PIB figures cite ~103,915 users in Andhra Pradesh and ~158,711 in
Odisha). **ORCA rides on top of this reach, it doesn't recreate it** — say this explicitly rather than
implying ORCA is a new distribution channel.
