"""Agent roster, tool allow-lists and the validated DAG spec (PLAN.md Phase 5.2-5.4).

Three things are enforced here, and each closes a specific way an agentic system goes
wrong:

* **Tool allow-lists.** Each agent may call only its registered tools. The safety node
  cannot call the routing tool; the response node cannot call anything that fetches data.
  Without this, "the planner decided to" becomes an explanation for any behaviour.
* **DAG validation.** A plan is checked against the roster and the tool registry before a
  single step runs: unknown nodes, unknown tools, tools the agent is not allowed, unknown
  dependencies and cycles are all rejected. This is what makes it safe to let a language
  model *propose* a plan — the proposal is a suggestion, and validation is the authority.
* **Budget pruning.** Steps that do not fit the budget are dropped before execution, and
  recorded as dropped.

The planner LLM emits only node names, dependencies and parameters. **It never emits a
number that reaches a user.** Numbers come from the Phase 4 kernels; the plan just says
which kernels to run and in what order.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from orca_schemas import OrcaModel
from pydantic import Field, model_validator

from orca_orchestrator.budget import QueryBudget, Spend


class AgentNode(StrEnum):
    """The agent roster (ARCHITECTURE.md §5)."""

    PLANNER = "planner"
    MARINE_DATA = "marine_data"
    WEATHER = "weather"
    GEOSPATIAL = "geospatial"
    ECOSYSTEM = "ecosystem"
    RISK = "risk"
    ROUTE = "route"
    RELIABILITY = "reliability"
    VERIFIER = "verifier"
    RESPONSE = "response"


class Tool(StrEnum):
    """Tools an agent may call. Deliberately coarse: one entry per capability."""

    FETCH_MARINE_DATA = "fetch_marine_data"
    FETCH_WEATHER = "fetch_weather"
    QUERY_GEOFENCE = "query_geofence"
    PREDICT_DRIFT = "predict_drift"
    RETRIEVE_CORPUS = "retrieve_corpus"
    QUERY_STRUCTURED_EVIDENCE = "query_structured_evidence"
    COMPUTE_SAFETY_SCORE = "compute_safety_score"
    RANK_FISHING_ZONES = "rank_fishing_zones"
    PLAN_ROUTE = "plan_route"
    DETECT_ANOMALIES = "detect_anomalies"
    SCORE_RELIABILITY = "score_reliability"
    SYNTHESISE_RESPONSE = "synthesise_response"


# Per-agent tool allow-lists (PLAN.md Phase 5.4). A node calling anything not listed here
# is a validation failure, not a runtime surprise.
TOOL_ALLOW_LIST: dict[AgentNode, frozenset[Tool]] = {
    AgentNode.PLANNER: frozenset(),
    AgentNode.MARINE_DATA: frozenset({Tool.FETCH_MARINE_DATA, Tool.QUERY_STRUCTURED_EVIDENCE}),
    AgentNode.WEATHER: frozenset({Tool.FETCH_WEATHER, Tool.QUERY_STRUCTURED_EVIDENCE}),
    AgentNode.GEOSPATIAL: frozenset({Tool.QUERY_GEOFENCE, Tool.PREDICT_DRIFT}),
    AgentNode.ECOSYSTEM: frozenset(
        {Tool.DETECT_ANOMALIES, Tool.RETRIEVE_CORPUS, Tool.QUERY_STRUCTURED_EVIDENCE}
    ),
    AgentNode.RISK: frozenset({Tool.COMPUTE_SAFETY_SCORE, Tool.RANK_FISHING_ZONES}),
    AgentNode.ROUTE: frozenset({Tool.PLAN_ROUTE, Tool.QUERY_GEOFENCE}),
    AgentNode.RELIABILITY: frozenset({Tool.SCORE_RELIABILITY}),
    AgentNode.VERIFIER: frozenset({Tool.RETRIEVE_CORPUS}),
    AgentNode.RESPONSE: frozenset({Tool.SYNTHESISE_RESPONSE}),
}

# Estimated cost per node, used for pre-execution pruning. Estimates, and named as such:
# the ledger records what was actually spent.
NODE_COST_ESTIMATE: dict[AgentNode, Spend] = {
    AgentNode.PLANNER: Spend(tokens=2_000, seconds=2.0, api_calls=0),
    AgentNode.MARINE_DATA: Spend(tokens=0, seconds=3.0, api_calls=2),
    AgentNode.WEATHER: Spend(tokens=0, seconds=3.0, api_calls=2),
    AgentNode.GEOSPATIAL: Spend(tokens=0, seconds=1.0, api_calls=1),
    AgentNode.ECOSYSTEM: Spend(tokens=1_500, seconds=4.0, api_calls=2),
    AgentNode.RISK: Spend(tokens=0, seconds=0.5, api_calls=0),
    AgentNode.ROUTE: Spend(tokens=0, seconds=2.0, api_calls=1),
    AgentNode.RELIABILITY: Spend(tokens=0, seconds=1.0, api_calls=1),
    AgentNode.VERIFIER: Spend(tokens=6_000, seconds=5.0, api_calls=1),
    AgentNode.RESPONSE: Spend(tokens=4_000, seconds=4.0, api_calls=0),
}

# Nodes that must never be pruned, whatever the budget. Dropping the verifier to save
# tokens would remove the check that decides whether to abstain — the one step whose
# absence changes a wrong answer into a confident wrong answer.
UNPRUNABLE: frozenset[AgentNode] = frozenset(
    {AgentNode.PLANNER, AgentNode.RISK, AgentNode.VERIFIER, AgentNode.RESPONSE}
)


class PlanStep(OrcaModel):
    """One node in the plan.

    ``extra='forbid'`` is inherited from ``OrcaModel``, so an LLM that invents a field is
    rejected rather than silently ignored.
    """

    node: AgentNode
    depends_on: tuple[AgentNode, ...] = ()
    tools: tuple[Tool, ...] = ()
    reason: str = Field(min_length=1, description="Why this step is in the plan.")
    optional: bool = Field(default=False, description="May be pruned under budget pressure.")

    @model_validator(mode="after")
    def _tools_are_allowed(self) -> Self:
        allowed = TOOL_ALLOW_LIST[self.node]
        forbidden = [t for t in self.tools if t not in allowed]
        if forbidden:
            names = ", ".join(sorted(t.value for t in forbidden))
            allow = ", ".join(sorted(t.value for t in allowed)) or "none"
            msg = f"{self.node} may not call {names}; its allow-list is: {allow}"
            raise ValueError(msg)
        if self.node in self.depends_on:
            msg = f"{self.node} cannot depend on itself"
            raise ValueError(msg)
        return self

    @property
    def estimated_cost(self) -> Spend:
        return NODE_COST_ESTIMATE[self.node]


class PlanSpec(OrcaModel):
    """A validated task DAG.

    This is the schema the planner LLM must produce. Validation happens on construction,
    so an invalid plan cannot exist as an object — there is no window in which unchecked
    LLM output is treated as a plan.
    """

    steps: tuple[PlanStep, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _dag_is_well_formed(self) -> Self:
        nodes = [s.node for s in self.steps]
        if len(nodes) != len(set(nodes)):
            duplicates = sorted({n.value for n in nodes if nodes.count(n) > 1})
            msg = f"each node may appear at most once; duplicated: {', '.join(duplicates)}"
            raise ValueError(msg)

        known = set(nodes)
        for step in self.steps:
            unknown = [d.value for d in step.depends_on if d not in known]
            if unknown:
                msg = (
                    f"{step.node} depends on node(s) not in the plan: {', '.join(sorted(unknown))}"
                )
                raise ValueError(msg)

        self._reject_cycles()
        return self

    def _reject_cycles(self) -> None:
        dependencies = {s.node: set(s.depends_on) for s in self.steps}
        resolved: set[AgentNode] = set()
        # Repeatedly peel off nodes whose dependencies are already resolved. If a pass
        # resolves nothing and work remains, the remainder is a cycle.
        while True:
            ready = {
                n for n, deps in dependencies.items() if n not in resolved and deps <= resolved
            }
            if not ready:
                break
            resolved |= ready
        remaining = set(dependencies) - resolved
        if remaining:
            cycle = ", ".join(sorted(n.value for n in remaining))
            msg = f"plan contains a dependency cycle among: {cycle}"
            raise ValueError(msg)

    def execution_layers(self) -> tuple[tuple[AgentNode, ...], ...]:
        """Group nodes into layers that can run in parallel (PLAN.md Phase 5.4).

        Each layer contains every node whose dependencies are satisfied by earlier
        layers, which is exactly the fan-out the architecture promises.
        """
        dependencies = {s.node: set(s.depends_on) for s in self.steps}
        layers: list[tuple[AgentNode, ...]] = []
        resolved: set[AgentNode] = set()
        while len(resolved) < len(dependencies):
            layer = sorted(
                (n for n, deps in dependencies.items() if n not in resolved and deps <= resolved),
                key=lambda n: n.value,
            )
            if not layer:  # pragma: no cover - construction rejects cycles
                break
            layers.append(tuple(layer))
            resolved |= set(layer)
        return tuple(layers)

    def estimated_cost(self) -> Spend:
        total = Spend()
        for step in self.steps:
            total = total + step.estimated_cost
        return total

    def step_for(self, node: AgentNode) -> PlanStep | None:
        for step in self.steps:
            if step.node is node:
                return step
        return None


class PrunedStep(OrcaModel):
    """A step dropped to fit the budget, kept for the trace."""

    node: AgentNode
    reason: str


class PrunedPlan(OrcaModel):
    """The plan that will actually run, plus what was dropped and why."""

    plan: PlanSpec
    pruned: tuple[PrunedStep, ...] = ()
    estimated_cost_tokens: int = 0
    estimated_cost_seconds: float = 0.0
    estimated_api_calls: int = 0

    @property
    def was_pruned(self) -> bool:
        return bool(self.pruned)


def prune_to_budget(plan: PlanSpec, budget: QueryBudget) -> PrunedPlan:
    """Drop optional steps until the estimated cost fits the budget.

    Drops the most expensive optional step first, which removes the overrun in the fewest
    drops and so keeps the most capability. Steps in :data:`UNPRUNABLE` are never dropped;
    if the plan still does not fit without them, it runs anyway and the ledger stops it
    mid-flight — better a truthful overrun than a plan with no verifier.

    Dependents of a dropped step are dropped too: a node whose input never arrives would
    otherwise run on nothing.
    """
    kept = list(plan.steps)
    pruned: list[PrunedStep] = []

    def total(steps: list[PlanStep]) -> Spend:
        spend = Spend()
        for step in steps:
            spend = spend + step.estimated_cost
        return spend

    while budget.exceeded_by(total(kept)) is not None:
        candidates = [s for s in kept if s.optional and s.node not in UNPRUNABLE]
        if not candidates:
            break
        victim = max(
            candidates,
            key=lambda s: (
                s.estimated_cost.tokens + s.estimated_cost.api_calls * 1000,
                s.node.value,
            ),
        )
        dimension = budget.exceeded_by(total(kept))
        kept.remove(victim)
        pruned.append(
            PrunedStep(
                node=victim.node,
                reason=f"dropped to fit the {dimension.value if dimension else 'query'} budget",
            )
        )

        # Anything depending on the dropped node cannot run meaningfully either.
        dropped_nodes = {p.node for p in pruned}
        orphans = [
            s for s in kept if set(s.depends_on) & dropped_nodes and s.node not in UNPRUNABLE
        ]
        for orphan in orphans:
            kept.remove(orphan)
            pruned.append(
                PrunedStep(
                    node=orphan.node,
                    reason=f"dropped because its dependency {victim.node.value} was pruned",
                )
            )

    # Dependencies on pruned nodes must be removed from surviving steps, or the plan
    # would not validate.
    surviving = {s.node for s in kept}
    cleaned = tuple(
        s.model_copy(update={"depends_on": tuple(d for d in s.depends_on if d in surviving)})
        for s in kept
    )
    final = PlanSpec(steps=cleaned, rationale=plan.rationale)
    estimate = final.estimated_cost()

    return PrunedPlan(
        plan=final,
        pruned=tuple(pruned),
        estimated_cost_tokens=estimate.tokens,
        estimated_cost_seconds=estimate.seconds,
        estimated_api_calls=estimate.api_calls,
    )
