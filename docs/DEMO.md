# DEMO.md — Demo Script & Evaluation Strategy

## 1. Demo script (highest-impact path, from the gap-closure report)

> A fisherman asks, in Tamil, by voice: **"Is it safe to go tomorrow morning, and where should I fish?"**
>
> 1. Planner decomposes the query into a task DAG.
> 2. Agents fetch INCOIS / IMD / CMEMS data in parallel.
> 3. **Boat-relative Safety Score = 72/100**, with a confidence band that decays past 3 days.
> 4. **Geofence warning:** "You would be 4 km from the Sri Lankan maritime boundary — stay west."
> 5. **Pareto fishing recommendation:** a higher catch-probability zone vs. a safer/cheaper zone —
>    shown as a trade-off, not a single point.
> 6. **Provenance panel** showing each data source and its age.
> 7. **Spoken Tamil answer** (multilingual synthesis).
>
> Then:
> 8. Trigger a **proactive cyclone alert** to demonstrate push (not pull) alerting.
> 9. Open the **researcher view** on "why has productivity declined here?" to show the causal DAG /
>    evidence-ranked hypothesis set.
> 10. Finish on the **deterministic replay** of the whole decision (provenance graph, re-run
>     deterministically).

## 2. Abstention demo (deliberate — a differentiator, not a weakness)

Rehearse a second, shorter demo where ORCA is fed stale or missing data and **refuses to give a safety
verdict**, e.g.: *"I can't safely answer; nearest reliable data is 6 h old."* For a life-safety,
government-facing system, calibrated refusal should be shown on purpose — it will impress a domain jury
more than a confident wrong answer.

## 3. Evaluation / testing strategy to have ready before the demo

- **Golden query set** — ~30 queries including the 5 canonical ones (safety, fishing zone, route,
  causal/why, geofence), each with documented expected behaviour.
- **Hindcast backtesting** — reliability layer tested against OMNI buoy records (or a synthetic hindcast
  if buoy data can't be assembled in time — see `PLAN.md` triggers).
- **Ablation testing** — run with/without the reliability layer and with/without the verifier, to show
  each component's value to the jury.
- **Abstention/hallucination test** — feed stale/missing data and confirm the system refuses rather than
  guessing (this is the test that backs demo section 2 above).

## 4. Mapping the demo to SIH evaluation dimensions

The gap-closure report notes SIH idea evaluation scores on: **novelty, complexity, clarity, feasibility,
practicability, sustainability, scale of impact, user experience, and future potential**; grand-finale
jurors add **technical feasibility, research quality, team dynamics**. Suggested mapping — build one
slide/demo beat per dimension:

| Dimension | Demo beat that addresses it |
|---|---|
| Novelty | Reliability Horizon (confidence decay), IMBL predictive drift, causal DAG |
| Complexity / technical feasibility | Multi-agent DAG execution shown live, provenance replay |
| Clarity | Explainable recommendation + provenance panel |
| Feasibility / practicability | Real live endpoints (INCOIS ERDDAP, IMD JSON, CMEMS) shown fetching |
| Sustainability | MPA/ban-zone/spawning-season hard constraints in the fishing recommendation |
| Scale of impact | "Rides on top of INCOIS's existing ~100k–160k users per coastal state" framing |
| User experience | Voice-first Tamil interaction, multilingual synthesis |
| Future potential | Scalability roadmap (prototype → regional pilot → operational scale → national layer) |
| Research quality | Golden query set, ablation results, hindcast backtesting numbers |

## 5. Opening framing line (say this explicitly on stage)

*"ORCA is the agentic reasoning and trust layer on top of INCOIS, IMD and ISRO's existing marine data —
not a new data source or a competing app. It quantifies how much to trust the forecasts you already
publish, and reasons across them the way a domain expert would."*

This pre-empts the most likely jury objection ("you're duplicating INCOIS") before it's asked.
