# METHODS.md — Concrete Methods, Formulas & Scientific Grounding

Everything here comes from the gap-closure report's "End-to-End Solution Flow / Build Blueprint" and
"Scientifically loose claims" sections. Use this file to replace vague PPT phrases ("7-point integrity
check," "Adversarial Reliability Nucleus," "Random Forest / Gradient Boosting" with no stated data) with
defensible, specific methods before presenting to a domain jury.

## 1. Decision kernels (deterministic — never delegate these to the LLM)

### Marine Safety Score
Weighted combination of:
- significant wave height (VHM0)
- wind speed
- swell period
- squall / lightning risk

Each driver is normalized against **vessel-class thresholds**, then multiplied by the **reliability
factor** for that region/lead-time. Output: a 0–100 score plus a confidence band. **Hard-abstain** if any
driver is stale beyond its cadence.

Example threshold bands per boat class: FRP vallam/catamaran — Hs < 1.5 m "caution," < 2 m "avoid";
mechanized trawler — higher thresholds. This is the **boat-capability-relative risk framing** — the same
2.5 m sea state is routine for a trawler and lethal for an FRP vallam, so the Safety Score must be
personalized by vessel class, engine power, range, freeboard and safety envelope (stored in agent memory).

### Fishing-Zone Intelligence
Pareto rank over: `{PFZ proximity/age, SST-front strength, chlorophyll gradient, distance/fuel cost,
safety score, IMBL/MPA/ban compliance}`. **Return the trade-off frontier, not a single point** — this is
the Multi-Objective Pareto Zone/Route Ranking differentiator.

### Reliability Horizon (flagship differentiator)
Per (region, variable, lead-time), learn skill by **backtesting forecasts against NIOT OMNI buoy
observations**, using:
- CRPS (Continuous Ranked Probability Score)
- Brier score
- reliability diagrams
- wrapped in **conformal prediction** for distribution-free interval coverage guarantees

Present every answer with a confidence that decays with horizon, e.g.: *"Tomorrow-morning wave forecast
here has been within ±0.4 m 90% of the time at 24 h lead; at 5-day lead, only 60%."*
This is the single most defensible, non-obvious differentiator — no Indian fisher system quantifies its
own trust. If historical forecast+observation pairs can't be assembled in time, ship it as a rigorous
design with a **synthetic-hindcast fallback** rather than dropping it.

### Route optimization
**Isochrone-A\*** (or orA*) over a gridded cost surface, where cell cost = `f(VHM0, current vector
projected on heading, wind, bathymetry/depth)`, with hard constraints from geofence polygons (IMBL
buffer, MPA no-take, ban zones) removing or penalizing cells. Published North-Indian-Ocean precedent:
Sen & Padhy 2015, *Applied Ocean Research*. Isochrone-A* / Isochrone-Dijkstra outperform naive
great-circle routing in published weather-routing benchmarks.

### Geofencing (PostGIS)
- Load EEZ (marineregions v12), the **agreed India–Sri Lanka IMBL geometry (1974/76 bilateral
  agreements)** — not a computed median line — and WDPA MPA polygons.
- `ST_Contains` / `ST_Intersects` for inside-restricted-zone checks.
- `ST_Distance` on geography type for true metric distance-to-IMBL.
- `ST_Buffer` to build graded warning bands (e.g. 5 km "amber," 2 km "red").
- Project future position from heading + current for **predictive drift warning** before a crossing
  happens — this directly addresses the 2025 Sri Lankan Navy arrests (346 fishermen / 44 trawlers,
  per Sri Lankan Navy statements reported in Indian press — cite as such, not as fact).

### Causal fish-productivity engine
For "why has productivity declined here?" queries: run a **constrained causal DAG / Granger-causality
analysis** over co-located time series:
- SST anomaly
- chlorophyll trend
- upwelling index
- marine-heatwave days
- freshwater flux
- fishing effort (from Global Fishing Watch)

Return an **evidence-ranked hypothesis set**, not a single LLM guess. Ground it in documented Arabian-Sea
science: warming → stratification → shift from diatoms to Noctiluca → altered productivity; rising
marine-heatwave days.

## 2. Provenance & Verifier (replaces "7-point integrity check" / "Adversarial Reliability Nucleus")

- Every agent decision logged as an auditable **provenance graph**: dataset → timestamp → agent →
  formula → output.
- Runs must be **deterministically replayable**.
- **Evidence-sufficiency gating with abstention**: if evidence is stale/insufficient/conflicting beyond a
  threshold, the system must abstain and say why (e.g. *"I can't safely answer; nearest reliable data is
  6 h old"*) rather than hallucinate. For a life-safety, government-facing system, calibrated refusal is a
  feature — rehearse this in the demo.

## 3. Scientific claims to correct before presenting

| Loose claim (as currently pitched) | Correction |
|---|---|
| PFZ = "where the fish are" | PFZ is a **probabilistic aggregation indicator** derived from SST fronts + chlorophyll, issued **3×/week** by INCOIS from NOAA-AVHRR, Eumetsat Met-Op, Oceansat-II and MODIS Aqua. Not real-time, not a fish census. Frame as "probability-of-aggregation, with confidence and age of advisory shown." |
| Oceansat-3 provides operational SST | MOSDAC states the EOS-06 SST Monitor (SSTM) "developed a technical problem in scan mechanism and therefore, not in operation at present." Do **not** claim live Oceansat-3 SST. Use INCOIS/CMEMS/NOAA SST; use Oceansat-3 only for chlorophyll (OCM-3) and winds (SCAT-3). |
| "Random Forest / Gradient Boosting" models, unspecified | A jury will ask what labels they're trained on and how they're validated. Either specify the labeled dataset + a hindcast validation plan, or reclassify these components as deterministic scoring rules. |
| Real-time satellite chlorophyll | Cloud cover and revisit gaps make ocean-colour intermittent. State that the system gap-fills/composites and always shows data age. |
| IMBL as a computed EEZ median line | The India–Sri Lanka maritime boundary is fixed by the **1974 and 1976 bilateral agreements**, not a computed median. Using a generated median line risks mis-warning fishermen — use the agreed boundary geometry. |

## 4. Evaluation / testing strategy (build this — it scores on feasibility & rigor)

- **Golden query set** (~30 queries incl. the 5 canonical ones) with expected behaviours documented.
- **Hindcast backtesting** of the reliability layer against OMNI buoy records.
- **Ablation testing** (with/without reliability layer, with/without verifier) to show each component's
  value.
- **Abstention/hallucination test**: feed stale or missing data and confirm the system refuses to answer
  rather than guessing.
