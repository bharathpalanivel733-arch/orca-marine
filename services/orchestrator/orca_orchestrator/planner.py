"""Planning: intent to validated DAG (PLAN.md Phase 5.3, 5.5).

Two planners behind one interface:

* :class:`RulePlanner` — deterministic, intent-driven. It is the **default**, not a
  fallback. For the five canonical query shapes the right DAG is known, and asking a
  language model to rediscover it each time would add cost, latency and variance for no
  gain. It also means the system plans correctly with no API key at all.
* :class:`LlmPlanner` — for genuinely novel or compound requests. Its output is parsed
  into :class:`PlanSpec`, which validates node names, tool allow-lists, dependencies and
  cycles. **Invalid output is rejected and the rule planner is used instead** — an LLM
  proposal is never trusted on the basis that it parsed.

Replanning (Phase 5.5) lives here too: when a node fails, the planner is asked for a
revised DAG that drops or substitutes the failed step. The substitution set is fixed and
declared, so a "dynamic replan" cannot invent a step the operator never approved.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from orca_orchestrator.plan import AgentNode, PlanSpec, PlanStep, Tool
from orca_orchestrator.state import Intent, ResolvedQuery

# Which node may stand in for which, when the first fails (PLAN.md Phase 5.5).
# Declared rather than inferred: a replan must only ever choose from options an operator
# has approved in advance.
APPROVED_SUBSTITUTIONS: dict[AgentNode, tuple[AgentNode, ...]] = {
    AgentNode.MARINE_DATA: (AgentNode.WEATHER,),
    AgentNode.WEATHER: (AgentNode.MARINE_DATA,),
    AgentNode.ECOSYSTEM: (),
    AgentNode.ROUTE: (),
    AgentNode.RELIABILITY: (),
}


def _step(
    node: AgentNode,
    *,
    depends_on: tuple[AgentNode, ...] = (),
    tools: tuple[Tool, ...] = (),
    reason: str,
    optional: bool = False,
) -> PlanStep:
    return PlanStep(
        node=node,
        depends_on=tuple(depends_on),
        tools=tuple(tools),
        reason=reason,
        optional=optional,
    )


class Planner(ABC):
    """Turns a resolved query into a validated DAG."""

    @abstractmethod
    def plan(self, query: ResolvedQuery) -> PlanSpec: ...

    @abstractmethod
    def replan(self, plan: PlanSpec, *, failed: AgentNode, reason: str) -> PlanSpec | None:
        """Revise a plan after a failure, or return None if it cannot continue."""


class RulePlanner(Planner):
    """Deterministic planner for the canonical intents."""

    def plan(self, query: ResolvedQuery) -> PlanSpec:
        builders = {
            Intent.SAFETY: self._safety_plan,
            Intent.FISHING_ZONE: self._fishing_plan,
            Intent.ROUTE: self._route_plan,
            Intent.GEOFENCE: self._geofence_plan,
            Intent.CAUSAL: self._causal_plan,
            Intent.UNKNOWN: self._minimal_plan,
        }
        return builders[query.intent]()

    def _safety_plan(self) -> PlanSpec:
        return PlanSpec(
            rationale="safety verdict: gather conditions, score against the vessel, verify, answer",
            steps=(
                _step(AgentNode.PLANNER, reason="decompose the request"),
                _step(
                    AgentNode.MARINE_DATA,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.FETCH_MARINE_DATA,),
                    reason="sea state for the requested box and window",
                ),
                _step(
                    AgentNode.WEATHER,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.FETCH_WEATHER,),
                    reason="wind and squall risk",
                ),
                _step(
                    AgentNode.RELIABILITY,
                    depends_on=(AgentNode.MARINE_DATA, AgentNode.WEATHER),
                    tools=(Tool.SCORE_RELIABILITY,),
                    reason="how much to trust the forecast at this lead time",
                ),
                _step(
                    AgentNode.GEOSPATIAL,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.QUERY_GEOFENCE, Tool.PREDICT_DRIFT),
                    reason="boundary proximity and predictive drift",
                    optional=True,
                ),
                _step(
                    AgentNode.RISK,
                    depends_on=(AgentNode.RELIABILITY,),
                    tools=(Tool.COMPUTE_SAFETY_SCORE,),
                    reason="boat-relative safety score",
                ),
                _step(
                    AgentNode.VERIFIER,
                    depends_on=(AgentNode.RISK,),
                    reason="evidence sufficiency and conflict check",
                ),
                _step(
                    AgentNode.RESPONSE,
                    depends_on=(AgentNode.VERIFIER,),
                    tools=(Tool.SYNTHESISE_RESPONSE,),
                    reason="explain the verdict with its provenance",
                ),
            ),
        )

    def _fishing_plan(self) -> PlanSpec:
        return PlanSpec(
            rationale="fishing zones: conditions plus PFZ evidence, ranked as a trade-off",
            steps=(
                _step(AgentNode.PLANNER, reason="decompose the request"),
                _step(
                    AgentNode.MARINE_DATA,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.FETCH_MARINE_DATA, Tool.QUERY_STRUCTURED_EVIDENCE),
                    reason="SST, chlorophyll and PFZ proximity",
                ),
                _step(
                    AgentNode.WEATHER,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.FETCH_WEATHER,),
                    reason="conditions on the grounds",
                ),
                _step(
                    AgentNode.GEOSPATIAL,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.QUERY_GEOFENCE,),
                    reason="IMBL, MPA and ban compliance per candidate zone",
                ),
                _step(
                    AgentNode.ECOSYSTEM,
                    depends_on=(AgentNode.MARINE_DATA,),
                    tools=(Tool.DETECT_ANOMALIES,),
                    reason="bloom or heatwave flags on the grounds",
                    optional=True,
                ),
                _step(
                    AgentNode.RISK,
                    depends_on=(AgentNode.WEATHER, AgentNode.GEOSPATIAL),
                    tools=(Tool.COMPUTE_SAFETY_SCORE, Tool.RANK_FISHING_ZONES),
                    reason="safety per zone, then the Pareto frontier",
                ),
                _step(AgentNode.VERIFIER, depends_on=(AgentNode.RISK,), reason="sufficiency check"),
                _step(
                    AgentNode.RESPONSE,
                    depends_on=(AgentNode.VERIFIER,),
                    tools=(Tool.SYNTHESISE_RESPONSE,),
                    reason="present the trade-off, not one point",
                ),
            ),
        )

    def _route_plan(self) -> PlanSpec:
        return PlanSpec(
            rationale="route: build a cost surface from conditions, respect geofences",
            steps=(
                _step(AgentNode.PLANNER, reason="decompose the request"),
                _step(
                    AgentNode.MARINE_DATA,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.FETCH_MARINE_DATA,),
                    reason="waves and currents for the cost surface",
                ),
                _step(
                    AgentNode.WEATHER,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.FETCH_WEATHER,),
                    reason="wind for the cost surface",
                ),
                _step(
                    AgentNode.GEOSPATIAL,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.QUERY_GEOFENCE,),
                    reason="hard constraints to remove cells",
                ),
                _step(
                    AgentNode.ROUTE,
                    depends_on=(AgentNode.MARINE_DATA, AgentNode.WEATHER, AgentNode.GEOSPATIAL),
                    tools=(Tool.PLAN_ROUTE,),
                    reason="isochrone-A* over the constrained surface",
                ),
                _step(
                    AgentNode.RISK,
                    depends_on=(AgentNode.ROUTE,),
                    tools=(Tool.COMPUTE_SAFETY_SCORE,),
                    reason="safety along the chosen route",
                ),
                _step(AgentNode.VERIFIER, depends_on=(AgentNode.RISK,), reason="sufficiency check"),
                _step(
                    AgentNode.RESPONSE,
                    depends_on=(AgentNode.VERIFIER,),
                    tools=(Tool.SYNTHESISE_RESPONSE,),
                    reason="explain the route and what it avoids",
                ),
            ),
        )

    def _geofence_plan(self) -> PlanSpec:
        return PlanSpec(
            rationale="boundary proximity and drift, answerable without a forecast",
            steps=(
                _step(AgentNode.PLANNER, reason="decompose the request"),
                _step(
                    AgentNode.GEOSPATIAL,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.QUERY_GEOFENCE, Tool.PREDICT_DRIFT),
                    reason="distance to boundary and time to crossing",
                ),
                _step(
                    AgentNode.RISK, depends_on=(AgentNode.GEOSPATIAL,), reason="framing the warning"
                ),
                _step(AgentNode.VERIFIER, depends_on=(AgentNode.RISK,), reason="sufficiency check"),
                _step(
                    AgentNode.RESPONSE,
                    depends_on=(AgentNode.VERIFIER,),
                    tools=(Tool.SYNTHESISE_RESPONSE,),
                    reason="state the distance and the bearing to stay clear",
                ),
            ),
        )

    def _causal_plan(self) -> PlanSpec:
        return PlanSpec(
            rationale="causal question: assemble drivers and return ranked hypotheses",
            steps=(
                _step(AgentNode.PLANNER, reason="decompose the request"),
                _step(
                    AgentNode.MARINE_DATA,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.QUERY_STRUCTURED_EVIDENCE,),
                    reason="SST, chlorophyll and upwelling time series",
                ),
                _step(
                    AgentNode.ECOSYSTEM,
                    depends_on=(AgentNode.MARINE_DATA,),
                    tools=(Tool.DETECT_ANOMALIES, Tool.RETRIEVE_CORPUS),
                    reason="anomaly flags and documented science",
                ),
                _step(
                    AgentNode.VERIFIER,
                    depends_on=(AgentNode.ECOSYSTEM,),
                    reason="sufficiency check",
                ),
                _step(
                    AgentNode.RESPONSE,
                    depends_on=(AgentNode.VERIFIER,),
                    tools=(Tool.SYNTHESISE_RESPONSE,),
                    reason="ranked hypotheses, not a single guess",
                ),
            ),
        )

    def _minimal_plan(self) -> PlanSpec:
        return PlanSpec(
            rationale="intent unclear: ask rather than guess",
            steps=(
                _step(AgentNode.PLANNER, reason="decompose the request"),
                _step(
                    AgentNode.RESPONSE,
                    depends_on=(AgentNode.PLANNER,),
                    tools=(Tool.SYNTHESISE_RESPONSE,),
                    reason="ask a clarifying question",
                ),
            ),
        )

    def replan(self, plan: PlanSpec, *, failed: AgentNode, reason: str) -> PlanSpec | None:
        """Drop the failed node, or substitute an approved alternative.

        Returns None when the failed node is load-bearing and has no substitute — the
        engine then abstains, which is the correct outcome. Replanning around a missing
        safety kernel would be inventing an answer.
        """
        from orca_orchestrator.plan import UNPRUNABLE

        if failed in UNPRUNABLE:
            return None

        substitutes = APPROVED_SUBSTITUTIONS.get(failed, ())
        present = {s.node for s in plan.steps}
        replacement = next((s for s in substitutes if s in present), None)

        kept = [s for s in plan.steps if s.node is not failed]
        if not kept:
            return None

        rewired = []
        for step in kept:
            depends = tuple(
                (replacement if d is failed and replacement is not None else d)
                for d in step.depends_on
                if d is not failed or replacement is not None
            )
            # Deduplicate in case the substitute was already a dependency.
            seen: list[AgentNode] = []
            for dependency in depends:
                if dependency not in seen and dependency is not step.node:
                    seen.append(dependency)
            rewired.append(step.model_copy(update={"depends_on": tuple(seen)}))

        note = f"replanned after {failed.value} failed ({reason})" + (
            f"; {replacement.value} covers it" if replacement else "; step dropped"
        )
        return PlanSpec(steps=tuple(rewired), rationale=f"{plan.rationale}; {note}")


@dataclass
class LlmPlanResponse:
    """Raw planner output, before validation."""

    content: str


class PlannerLlm(ABC):
    """The model call behind :class:`LlmPlanner`. Injected so tests need no API key."""

    @abstractmethod
    def propose_plan(self, query: ResolvedQuery) -> LlmPlanResponse: ...


class LlmPlanner(Planner):
    """Planner for novel requests, with the rule planner as its safety net."""

    def __init__(self, llm: PlannerLlm, fallback: Planner | None = None) -> None:
        self._llm = llm
        self._fallback = fallback or RulePlanner()
        self.last_rejection: str | None = None

    def plan(self, query: ResolvedQuery) -> PlanSpec:
        """Propose, validate, and fall back to rules on any problem.

        Every failure mode — malformed JSON, an unknown node, a tool outside the agent's
        allow-list, a cycle — lands in the same place: use the deterministic plan. The
        rejection is recorded on ``last_rejection`` so the trace can show it.
        """
        self.last_rejection = None
        try:
            payload = json.loads(self._llm.propose_plan(query).content)
        except (json.JSONDecodeError, TypeError) as exc:
            self.last_rejection = f"planner output was not valid JSON: {exc}"
            return self._fallback.plan(query)

        try:
            return PlanSpec.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 - any validation failure means fall back
            self.last_rejection = f"planner output failed validation: {exc}"
            return self._fallback.plan(query)

    def replan(self, plan: PlanSpec, *, failed: AgentNode, reason: str) -> PlanSpec | None:
        # Replanning is mechanical and safety-relevant, so it stays deterministic even
        # when the original plan came from a model.
        return self._fallback.replan(plan, failed=failed, reason=reason)
