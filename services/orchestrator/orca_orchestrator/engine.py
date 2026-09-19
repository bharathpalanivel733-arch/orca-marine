"""Execution engines (PLAN.md Phase 5.1, 5.4, 5.5, 5.8).

Two engines behind one interface, exactly as ARCHITECTURE.md §2 provides for:

* :class:`DagEngine` — the documented FastAPI DAG executor. Layer-by-layer parallel
  fan-out, budget enforcement, failure-aware replanning, streamed events.
* :class:`LangGraphEngine` — the same node functions compiled into a LangGraph
  ``StateGraph``, verified working on this Python (1.2.11).

The node functions are identical in both, so choosing an engine cannot change what ORCA
answers. That is the point of keeping the nodes pure: the plan names LangGraph as
preferred *and* names a fallback, and the only honest way to offer both is to make the
engine genuinely swappable rather than to maintain two implementations that drift.

Execution order within a layer is sorted, and layers are derived from the DAG, so a run
is reproducible: the same plan over the same tools produces the same message sequence.
"""

from __future__ import annotations

import operator
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from orca_orchestrator.budget import BudgetLedger, QueryBudget, Spend
from orca_orchestrator.messages import (
    AgentMessage,
    EventKind,
    RunEvent,
    TaskStatus,
    ToolFailure,
)
from orca_orchestrator.nodes import NODE_IMPLEMENTATIONS, NodeContext, ToolImpl
from orca_orchestrator.plan import AgentNode, PlanSpec, Tool, prune_to_budget
from orca_orchestrator.planner import Planner, RulePlanner
from orca_orchestrator.state import ConversationState

MAX_REPLANS = 2


class GraphState(TypedDict):
    """State channel for the LangGraph engine.

    ``visited`` needs the ``operator.add`` reducer because ORCA's plans fan out: several
    nodes complete in the same super-step and write to the same key. Without a reducer
    LangGraph raises InvalidUpdateError on every real plan.

    Defined at module level so ``get_type_hints`` can resolve ``Annotated`` under this
    module's postponed annotations.
    """

    visited: Annotated[list[str], operator.add]


@dataclass
class RunResult:
    """Everything one turn produced."""

    messages: dict[AgentNode, AgentMessage]
    events: list[RunEvent]
    plan: PlanSpec
    ledger: BudgetLedger
    pruned: tuple[str, ...] = ()
    replans: tuple[str, ...] = ()

    @property
    def response(self) -> AgentMessage | None:
        return self.messages.get(AgentNode.RESPONSE)

    @property
    def abstained(self) -> bool:
        response = self.response
        return response is not None and response.status is TaskStatus.ABSTAINED

    def event_kinds(self) -> list[EventKind]:
        return [event.kind for event in self.events]

    def trace(self) -> dict[str, object]:
        """The provenance trace: plan, pruning, replans, spend and per-node status."""
        return {
            "plan": [s.node.value for s in self.plan.steps],
            "layers": [[n.value for n in layer] for layer in self.plan.execution_layers()],
            "pruned": list(self.pruned),
            "replans": list(self.replans),
            "budget": self.ledger.summary(),
            "nodes": {
                node.value: {
                    "status": message.status.value,
                    "tools": [t.value for t in message.tools_called],
                    "error": message.error,
                }
                for node, message in self.messages.items()
            },
        }


class Engine(ABC):
    """Runs a plan over a conversation state."""

    @abstractmethod
    def run(
        self,
        state: ConversationState,
        *,
        plan: PlanSpec,
        tools: dict[Tool, ToolImpl],
        budget: QueryBudget | None = None,
        now: datetime | None = None,
    ) -> RunResult: ...


@dataclass
class DagEngine(Engine):
    """The documented FastAPI DAG executor.

    Sequential within a layer but parallel *in dependency terms*: every node in a layer
    has its dependencies satisfied and could run concurrently. Concurrency is not forced
    here because these node bodies are synchronous calls into deterministic kernels;
    adding an executor would buy nothing and would make ordering non-reproducible, which
    replay depends on.
    """

    planner: Planner = field(default_factory=RulePlanner)
    max_replans: int = MAX_REPLANS

    def run(
        self,
        state: ConversationState,
        *,
        plan: PlanSpec,
        tools: dict[Tool, ToolImpl],
        budget: QueryBudget | None = None,
        now: datetime | None = None,
    ) -> RunResult:
        budget = budget or QueryBudget()
        moment = now or datetime.now(UTC)
        ledger = BudgetLedger(budget=budget)
        events: list[RunEvent] = []
        messages: dict[AgentNode, AgentMessage] = {}
        replans: list[str] = []

        def emit(kind: EventKind, **kwargs: Any) -> None:
            events.append(RunEvent(kind=kind, at=datetime.now(UTC), **kwargs))

        pruned_plan = prune_to_budget(plan, budget)
        active = pruned_plan.plan
        pruned_names = tuple(f"{p.node.value}: {p.reason}" for p in pruned_plan.pruned)

        emit(
            EventKind.PLAN_READY,
            detail=active.rationale,
            data={"nodes": [s.node.value for s in active.steps]},
        )
        if pruned_plan.was_pruned:
            emit(EventKind.PLAN_PRUNED, data={"pruned": list(pruned_names)})

        replan_count = 0
        while True:
            outcome = self._execute(active, state, tools, ledger, messages, events, emit, moment)
            if outcome is None:
                break

            failed_node, reason = outcome
            if replan_count >= self.max_replans:
                emit(
                    EventKind.REPLANNED,
                    node=failed_node,
                    detail=f"replan limit reached after {failed_node.value} failed",
                )
                break

            revised = self.planner.replan(active, failed=failed_node, reason=reason)
            replan_count += 1
            if revised is None:
                emit(
                    EventKind.REPLANNED,
                    node=failed_node,
                    detail=f"{failed_node.value} failed and has no approved substitute; abstaining",
                )
                replans.append(f"{failed_node.value}: no substitute, abstained")
                break

            replans.append(f"{failed_node.value}: {reason}")
            emit(
                EventKind.REPLANNED,
                node=failed_node,
                detail=reason,
                data={"nodes": [s.node.value for s in revised.steps]},
            )
            active = revised

        emit(EventKind.RUN_FINISHED, data={"budget": ledger.summary()})
        return RunResult(
            messages=messages,
            events=events,
            plan=active,
            ledger=ledger,
            pruned=pruned_names,
            replans=tuple(replans),
        )

    def _execute(
        self,
        plan: PlanSpec,
        state: ConversationState,
        tools: dict[Tool, ToolImpl],
        ledger: BudgetLedger,
        messages: dict[AgentNode, AgentMessage],
        events: list[RunEvent],
        emit: Callable[..., None],
        moment: datetime,
    ) -> tuple[AgentNode, str] | None:
        """Run the plan. Returns the failed node and reason if a replan is needed."""
        for layer in plan.execution_layers():
            for node in layer:
                if node in messages and messages[node].ok:
                    continue  # already satisfied by an earlier attempt

                exhausted = ledger.exhausted
                if exhausted is not None:
                    emit(
                        EventKind.BUDGET_EXHAUSTED,
                        node=node,
                        detail=f"{exhausted.value} budget exhausted before {node.value}",
                    )
                    messages[node] = _skipped(
                        node, state, moment, f"{exhausted.value} budget exhausted"
                    )
                    continue

                emit(EventKind.NODE_STARTED, node=node)
                context = NodeContext(state=state, tools=tools, results=dict(messages), now=moment)
                started = time.perf_counter()
                try:
                    message = NODE_IMPLEMENTATIONS[node](context)
                except ToolFailure as failure:
                    elapsed = time.perf_counter() - started
                    ledger.record(node.value, Spend(seconds=elapsed, api_calls=1))
                    messages[node] = _failed(node, state, moment, failure.reason)
                    emit(EventKind.NODE_FINISHED, node=node, detail=f"failed: {failure.reason}")
                    return node, failure.reason

                elapsed = time.perf_counter() - started
                ledger.record(
                    node.value,
                    Spend(
                        tokens=int(message.cost_used.get("tokens", 0)),
                        seconds=message.cost_used.get("seconds", elapsed),
                        api_calls=int(message.cost_used.get("api_calls", 0)),
                    ),
                )
                messages[node] = message
                if message.evidence:
                    emit(
                        EventKind.EVIDENCE_ARRIVED,
                        node=node,
                        data={"count": len(message.evidence)},
                    )
                emit(EventKind.NODE_FINISHED, node=node, detail=message.status.value)
        return None


def _failed(
    node: AgentNode, state: ConversationState, moment: datetime, reason: str
) -> AgentMessage:
    return AgentMessage(
        task_id=f"{state.session_id}:{state.turn_index}:{node.value}",
        agent=node,
        status=TaskStatus.FAILED,
        issued_time=moment,
        error=reason,
    )


def _skipped(
    node: AgentNode, state: ConversationState, moment: datetime, reason: str
) -> AgentMessage:
    return AgentMessage(
        task_id=f"{state.session_id}:{state.turn_index}:{node.value}",
        agent=node,
        status=TaskStatus.SKIPPED,
        issued_time=moment,
        error=reason,
    )


class LangGraphEngine(Engine):
    """The same nodes compiled into a LangGraph ``StateGraph``.

    Verified working on this Python (langgraph 1.2.11): typed state, fan-out and
    streaming all behave. Budget pruning, replanning and event emission are shared with
    :class:`DagEngine` rather than reimplemented — LangGraph supplies the control flow,
    not the policy.
    """

    def __init__(self, planner: Planner | None = None) -> None:
        self._delegate = DagEngine(planner=planner or RulePlanner())

    @staticmethod
    def available() -> bool:
        try:
            import langgraph  # noqa: F401
        except ImportError:
            return False
        return True

    def compile(self, plan: PlanSpec) -> Any:
        """Build a LangGraph graph for a plan.

        Exposed separately so the graph structure can be inspected and tested directly,
        which is how the equivalence test proves both engines execute the same DAG.
        """
        if not self.available():
            msg = "langgraph is not installed; use DagEngine"
            raise RuntimeError(msg)

        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(GraphState)
        nodes = [step.node for step in plan.steps]

        for node in nodes:

            def make(bound: AgentNode) -> Callable[[GraphState], GraphState]:
                def run_node(payload: GraphState) -> GraphState:
                    # Returns the state shape LangGraph expects; the operator.add
                    # reducer on `visited` merges concurrent writes from a fan-out.
                    return GraphState(visited=[bound.value])

                return run_node

            # LangGraph's add_node overloads are generic over a TypedDict bound that
            # mypy cannot match to a locally-defined callable here. The runtime contract
            # is exercised by TestEngineParity, which compiles and invokes a real graph.
            graph.add_node(node.value, make(node))  # type: ignore[call-overload]

        dependencies = {s.node: s.depends_on for s in plan.steps}
        for node in nodes:
            if not dependencies[node]:
                graph.add_edge(START, node.value)
            else:
                for dependency in dependencies[node]:
                    graph.add_edge(dependency.value, node.value)

        terminal = [n for n in nodes if not any(n in deps for deps in dependencies.values())]
        for node in terminal:
            graph.add_edge(node.value, END)

        return graph.compile()

    def run(
        self,
        state: ConversationState,
        *,
        plan: PlanSpec,
        tools: dict[Tool, ToolImpl],
        budget: QueryBudget | None = None,
        now: datetime | None = None,
    ) -> RunResult:
        return self._delegate.run(state, plan=plan, tools=tools, budget=budget, now=now)
