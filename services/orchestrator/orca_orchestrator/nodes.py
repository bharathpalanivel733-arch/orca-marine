"""Agent node implementations (PLAN.md Phase 5.2).

Nodes are **pure functions of a context**, deliberately engine-agnostic: the same
function runs under LangGraph and under the FastAPI fallback executor, so swapping the
engine cannot change behaviour. Each returns an :class:`AgentMessage`.

Tool access is checked here at call time as well as at plan-validation time. Belt and
braces is warranted: validation checks the plan the planner *wrote*, this checks the call
a node actually *makes*, and only the second catches a coding error in a node.

The numeric nodes (risk, route) delegate to the Phase 4 kernels and pass their results
through untouched. **No node computes a safety number itself**, and the response node —
the only one that will later hold an LLM — receives kernel results and is responsible for
wording alone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from orca_orchestrator.budget import Spend
from orca_orchestrator.messages import AgentMessage, TaskStatus, ToolFailure
from orca_orchestrator.plan import TOOL_ALLOW_LIST, AgentNode, Tool
from orca_orchestrator.state import ConversationState

ToolImpl = Callable[..., Any]


@dataclass
class NodeContext:
    """What a node is given to work with.

    ``tools`` holds implementations by name. Nodes never import a data source directly —
    everything arrives through this registry, which is what makes allow-lists meaningful
    and tests able to inject failures.
    """

    state: ConversationState
    tools: dict[Tool, ToolImpl] = field(default_factory=dict)
    results: dict[AgentNode, AgentMessage] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def call(self, node: AgentNode, tool: Tool, /, **kwargs: Any) -> Any:
        """Invoke a tool on behalf of a node, enforcing its allow-list.

        A tool that is registered but not allowed raises ``PermissionError`` rather than
        ``ToolFailure``: an agent reaching outside its allow-list is a defect in ORCA, not
        an upstream outage, and must not be silently replanned around.
        """
        if tool not in TOOL_ALLOW_LIST[node]:
            allowed = ", ".join(sorted(t.value for t in TOOL_ALLOW_LIST[node])) or "none"
            msg = f"{node.value} is not allowed to call {tool.value}; allow-list: {allowed}"
            raise PermissionError(msg)
        impl = self.tools.get(tool)
        if impl is None:
            raise ToolFailure(node, f"tool {tool.value} is not registered")
        return impl(**kwargs)

    def evidence_from(self, *nodes: AgentNode) -> tuple[str, ...]:
        """Collect evidence ids produced by upstream nodes."""
        collected: list[str] = []
        for node in nodes:
            message = self.results.get(node)
            if message is not None:
                collected.extend(message.evidence)
        return tuple(dict.fromkeys(collected))


def _message(
    node: AgentNode,
    *,
    context: NodeContext,
    status: TaskStatus = TaskStatus.SUCCEEDED,
    result: dict[str, Any] | None = None,
    evidence: tuple[str, ...] = (),
    sources: tuple[str, ...] = (),
    confidence: float | None = None,
    tools_called: tuple[Tool, ...] = (),
    cost: Spend | None = None,
    error: str | None = None,
) -> AgentMessage:
    spend = cost or Spend()
    return AgentMessage(
        task_id=f"{context.state.session_id}:{context.state.turn_index}:{node.value}",
        agent=node,
        status=status,
        inputs={"turn": context.state.turn_index},
        evidence=evidence,
        result=result,
        confidence=confidence,
        sources=sources,
        issued_time=context.now,
        cost_used={
            "tokens": float(spend.tokens),
            "seconds": spend.seconds,
            "api_calls": float(spend.api_calls),
        },
        tools_called=tools_called,
        error=error,
    )


def planner_node(context: NodeContext) -> AgentMessage:
    """Record the resolved query and what was carried over."""
    resolved = context.state.resolved
    return _message(
        AgentNode.PLANNER,
        context=context,
        result={
            "intent": resolved.intent.value if resolved else "unknown",
            "carried_slots": list(resolved.carried_slots) if resolved else [],
            "missing_required": list(resolved.missing_required) if resolved else [],
        },
        cost=Spend(tokens=1_200, seconds=0.5),
    )


def marine_data_node(context: NodeContext) -> AgentMessage:
    """Fetch sea-state evidence through the ingest layer."""
    payload = context.call(AgentNode.MARINE_DATA, Tool.FETCH_MARINE_DATA)
    return _message(
        AgentNode.MARINE_DATA,
        context=context,
        result=payload,
        evidence=tuple(payload.get("evidence_ids", ())),
        sources=tuple(payload.get("sources", ())),
        tools_called=(Tool.FETCH_MARINE_DATA,),
        cost=Spend(seconds=1.0, api_calls=1),
    )


def weather_node(context: NodeContext) -> AgentMessage:
    """Fetch wind, squall and cyclone evidence."""
    payload = context.call(AgentNode.WEATHER, Tool.FETCH_WEATHER)
    return _message(
        AgentNode.WEATHER,
        context=context,
        result=payload,
        evidence=tuple(payload.get("evidence_ids", ())),
        sources=tuple(payload.get("sources", ())),
        tools_called=(Tool.FETCH_WEATHER,),
        cost=Spend(seconds=1.0, api_calls=1),
    )


def geospatial_node(context: NodeContext) -> AgentMessage:
    """Boundary proximity and predictive drift, from the Phase 2 geofence."""
    payload = context.call(AgentNode.GEOSPATIAL, Tool.QUERY_GEOFENCE)
    return _message(
        AgentNode.GEOSPATIAL,
        context=context,
        result=payload,
        sources=tuple(payload.get("sources", ())),
        tools_called=(Tool.QUERY_GEOFENCE,),
        cost=Spend(seconds=0.4, api_calls=1),
    )


def ecosystem_node(context: NodeContext) -> AgentMessage:
    """Anomaly flags and supporting literature."""
    payload = context.call(AgentNode.ECOSYSTEM, Tool.DETECT_ANOMALIES)
    return _message(
        AgentNode.ECOSYSTEM,
        context=context,
        result=payload,
        evidence=tuple(payload.get("evidence_ids", ())),
        tools_called=(Tool.DETECT_ANOMALIES,),
        cost=Spend(tokens=800, seconds=1.5, api_calls=1),
    )


def reliability_node(context: NodeContext) -> AgentMessage:
    """How much to trust the forecast at this lead time."""
    payload = context.call(AgentNode.RELIABILITY, Tool.SCORE_RELIABILITY)
    return _message(
        AgentNode.RELIABILITY,
        context=context,
        result=payload,
        confidence=payload.get("reliability"),
        tools_called=(Tool.SCORE_RELIABILITY,),
        cost=Spend(seconds=0.3, api_calls=1),
    )


def risk_node(context: NodeContext) -> AgentMessage:
    """Run the Phase 4 safety kernel and pass its result through unaltered."""
    payload = context.call(AgentNode.RISK, Tool.COMPUTE_SAFETY_SCORE)
    abstained = bool(payload.get("abstained"))
    return _message(
        AgentNode.RISK,
        context=context,
        status=TaskStatus.ABSTAINED if abstained else TaskStatus.SUCCEEDED,
        result=payload,
        evidence=context.evidence_from(AgentNode.MARINE_DATA, AgentNode.WEATHER),
        tools_called=(Tool.COMPUTE_SAFETY_SCORE,),
        cost=Spend(seconds=0.2),
    )


def route_node(context: NodeContext) -> AgentMessage:
    """Run the Phase 4 router over the constrained cost surface."""
    payload = context.call(AgentNode.ROUTE, Tool.PLAN_ROUTE)
    abstained = bool(payload.get("abstained"))
    return _message(
        AgentNode.ROUTE,
        context=context,
        status=TaskStatus.ABSTAINED if abstained else TaskStatus.SUCCEEDED,
        result=payload,
        tools_called=(Tool.PLAN_ROUTE,),
        cost=Spend(seconds=0.8, api_calls=1),
    )


def verifier_node(context: NodeContext) -> AgentMessage:
    """Evidence-sufficiency gate.

    Deterministic rules decide; an LLM critique is layered on later (Phase 6.1) and can
    only *add* doubt, never remove it. Abstains when an upstream kernel abstained or when
    a required node failed — the gate's job is to stop a thin answer reaching a user.
    """
    upstream_abstained = [
        node.value
        for node, message in context.results.items()
        if message.status is TaskStatus.ABSTAINED
    ]
    failures = [
        node.value
        for node, message in context.results.items()
        if message.status is TaskStatus.FAILED
    ]

    if upstream_abstained or failures:
        reasons = []
        if upstream_abstained:
            reasons.append(f"upstream abstained: {', '.join(sorted(upstream_abstained))}")
        if failures:
            reasons.append(f"failed: {', '.join(sorted(failures))}")
        return _message(
            AgentNode.VERIFIER,
            context=context,
            status=TaskStatus.ABSTAINED,
            result={"sufficient": False, "reasons": reasons},
            cost=Spend(tokens=900, seconds=0.5),
        )

    evidence = context.evidence_from(*context.results.keys())
    return _message(
        AgentNode.VERIFIER,
        context=context,
        result={"sufficient": True, "evidence_count": len(evidence)},
        evidence=evidence,
        confidence=1.0 if evidence else 0.5,
        cost=Spend(tokens=900, seconds=0.5),
    )


def response_node(context: NodeContext) -> AgentMessage:
    """Assemble the answer.

    This is where an LLM will narrate (Phase 7.5), and it is given **only** kernel
    results and provenance to narrate. It has no tool that can compute or alter a number,
    which is enforced by its allow-list containing exactly one synthesis tool.
    """
    verifier = context.results.get(AgentNode.VERIFIER)
    resolved = context.state.resolved
    sufficient = bool(verifier and verifier.result and verifier.result.get("sufficient"))

    if resolved is not None and resolved.needs_clarification:
        return _message(
            AgentNode.RESPONSE,
            context=context,
            result={
                "kind": "clarifying_question",
                "missing": list(resolved.missing_required),
            },
            cost=Spend(tokens=600, seconds=0.4),
        )

    if not sufficient:
        return _message(
            AgentNode.RESPONSE,
            context=context,
            status=TaskStatus.ABSTAINED,
            result={
                "kind": "abstention",
                "reasons": (verifier.result or {}).get("reasons", []) if verifier else [],
            },
            cost=Spend(tokens=600, seconds=0.4),
        )

    return _message(
        AgentNode.RESPONSE,
        context=context,
        result={
            "kind": "answer",
            "carried_slots": list(resolved.carried_slots) if resolved else [],
            "kernel_results": {
                node.value: message.result
                for node, message in context.results.items()
                if node in {AgentNode.RISK, AgentNode.ROUTE}
            },
        },
        evidence=context.evidence_from(*context.results.keys()),
        cost=Spend(tokens=2_500, seconds=1.5),
    )


NODE_IMPLEMENTATIONS: dict[AgentNode, Callable[[NodeContext], AgentMessage]] = {
    AgentNode.PLANNER: planner_node,
    AgentNode.MARINE_DATA: marine_data_node,
    AgentNode.WEATHER: weather_node,
    AgentNode.GEOSPATIAL: geospatial_node,
    AgentNode.ECOSYSTEM: ecosystem_node,
    AgentNode.RELIABILITY: reliability_node,
    AgentNode.RISK: risk_node,
    AgentNode.ROUTE: route_node,
    AgentNode.VERIFIER: verifier_node,
    AgentNode.RESPONSE: response_node,
}
