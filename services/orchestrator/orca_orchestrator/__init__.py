"""ORCA agentic orchestration (PLAN.md Phase 5).

A typed, stateful task DAG over the agent roster, with tool allow-lists, validated plans,
bounded budgets, failure-aware replanning, multi-turn entity carry-over and streamable
progress events.

The first LLM in the system appears here — and only outside the numeric path. It may
*propose* a plan, which is then validated against the roster and the tool registry before
anything runs, and it may later narrate results. Every number still comes from the Phase 4
kernels.
"""

from orca_orchestrator.budget import BudgetDimension, BudgetLedger, QueryBudget, Spend
from orca_orchestrator.engine import DagEngine, Engine, LangGraphEngine, RunResult
from orca_orchestrator.messages import (
    AgentMessage,
    EventKind,
    RunEvent,
    TaskStatus,
    ToolFailure,
)
from orca_orchestrator.nodes import NODE_IMPLEMENTATIONS, NodeContext
from orca_orchestrator.plan import (
    NODE_COST_ESTIMATE,
    TOOL_ALLOW_LIST,
    UNPRUNABLE,
    AgentNode,
    PlanSpec,
    PlanStep,
    PrunedPlan,
    Tool,
    prune_to_budget,
)
from orca_orchestrator.planner import (
    APPROVED_SUBSTITUTIONS,
    LlmPlanner,
    LlmPlanResponse,
    Planner,
    PlannerLlm,
    RulePlanner,
)
from orca_orchestrator.state import (
    ConversationState,
    Intent,
    ResolvedQuery,
    SessionMemory,
    Slot,
    SlotOrigin,
    TurnRequest,
)

SERVICE_NAME = "orca-orchestrator"
__version__ = "0.1.0"

__all__ = [
    "APPROVED_SUBSTITUTIONS",
    "NODE_COST_ESTIMATE",
    "NODE_IMPLEMENTATIONS",
    "SERVICE_NAME",
    "TOOL_ALLOW_LIST",
    "UNPRUNABLE",
    "AgentMessage",
    "AgentNode",
    "BudgetDimension",
    "BudgetLedger",
    "ConversationState",
    "DagEngine",
    "Engine",
    "EventKind",
    "Intent",
    "LangGraphEngine",
    "LlmPlanResponse",
    "LlmPlanner",
    "NodeContext",
    "PlanSpec",
    "PlanStep",
    "Planner",
    "PlannerLlm",
    "PrunedPlan",
    "QueryBudget",
    "ResolvedQuery",
    "RulePlanner",
    "RunEvent",
    "RunResult",
    "SessionMemory",
    "Slot",
    "SlotOrigin",
    "Spend",
    "TaskStatus",
    "Tool",
    "ToolFailure",
    "TurnRequest",
    "__version__",
    "prune_to_budget",
]
