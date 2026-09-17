# SPEC.md — ORCA Problem Statement & Requirements

Team: **OCEAN-IQ** · Problem Statement ID: **SIH26176** · Title: **ORCA — Marine EcOsystem Reasoning with Collaborative Agents**
Theme: **Disaster Management** · Category: **Software**

## 1. What ORCA is

ORCA ("Your Intelligent Marine Decision Companion") is an agentic AI system that takes a natural-language
question from a stakeholder (mainly fisherfolk) and returns an **evidence-grounded, explainable marine
decision** — where to fish, whether it's safe to sail, which route to take — instead of a plain LLM answer.

Core pipeline (from the PPT):

```
Natural-Language Query → Context Extraction → Evidence Discovery → Specialized Marine Analysis
→ Verification & Fusion → Decision → Explainable Recommendation
```

Key positioning (from the gap-closure report): **ORCA is the agentic reasoning-and-trust layer on top of
INCOIS/IMD/ISRO feeds — it is not a competing data source or app.** Existing systems (SAMUDRA, GEMINI,
mKRISHI, Fisher Friend, Jal Anveshak) already deliver forecasts and advisories; ORCA reasons about them,
fuses them, and tells you how much to trust them. State this explicitly on stage — it pre-empts the
"you're duplicating INCOIS" jury objection.

## 2. The gap ORCA claims to close (from the PPT)

| Data available | Where it lives | Fisherfolk's real question it still can't answer | Why it still fails |
|---|---|---|---|
| Satellite EO, SST, Chlorophyll | ISRO / INCOIS platforms | Where should I go? | No correlation |
| Waves, Currents, Weather | IMD forecast systems | When should I go? | No fusion with timing |
| Fisheries indicators, PFZ | Fisheries advisory feeds | Is the sea condition favourable? | No safety cross-check |
| Tides, GIS layers, Advisories | Separate formats & resolutions | Which option is actually feasible? | No unified reasoning |

## 3. Official problem-statement clauses (from the gap-closure report)

The statement requires all of the following. Use this as the master checklist — the jury scores against
each clause directly.

- (a) Agentic autonomous planning + task decomposition
- (b) Multi-agent collaboration
- (c) Tool selection
- (d) Spatio-temporal reasoning
- (e) Explainable / evidence-based recommendations
- (f) Multilingual support in Indian regional languages
- (g) Multi-turn conversation + query refinement
- (h) Proactive safety alerts (cyclone, lightning, high waves)
- (i) Geofencing near IMBL / restricted waters / MPAs
- (j) Route optimization + safe navigation
- (k) Causal questions (e.g. "why has fish productivity declined")
- (l) Disaster Management theme fit
- Stakeholder coverage: fishermen, researchers, coastal authorities, disaster agencies, maritime operators

See `CLAIMS.md` for the clause-by-clause gap analysis (what ORCA already covers vs. what's missing).

## 4. Stakeholders / personas (from the PPT + gap report)

1. **Fisherfolk** — safety, "where/when should I go" queries, voice-first, regional language.
2. **Marine safety** — hazard visibility, no false "safe to sail" assurance.
3. **Government / coastal ops / disaster agencies** — aggregate risk map, fleet-at-sea count, alert
   dissemination.
4. **Researchers** — raw data + provenance + export, causal/ecosystem reasoning.
5. **Maritime operators** — port warnings, sea-area bulletins, route ETAs.
6. **Ecosystem management** — MPA/regulatory compliance.

## 5. Evaluation dimensions to design against

From the gap-closure report, SIH idea evaluation scores on: novelty, complexity, clarity, feasibility,
practicability, sustainability, scale of impact, user experience, and future potential. Grand-finale
jurors add: technical feasibility, research quality, team dynamics. `DEMO.md` maps the demo script to
these dimensions.

## 6. Non-goals / things NOT to rebuild

Per the gap-closure report, do **not** reinvent:
- INCOIS's own advisory delivery (PFZ advisories, 5–10-day Ocean State Forecast, SAMUDRA app)
- GEMINI (GAGAN/NavIC deep-sea alerts), DAT-SG distress transmitter, Sagarmitra (offline/distress delivery)
- SMS/WhatsApp/Telegram advisory reach (already run by INCOIS at ~100k–160k users per coastal state)

ORCA should integrate with / sit on top of these rather than duplicate them, and should say so on stage.
