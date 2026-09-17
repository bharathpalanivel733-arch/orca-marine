"""ORCA agent mesh.

Phase 0 establishes the package and its shared-contract wiring only. The agent
roster (planner, marine data, weather, geofence, causal, safety, route, reliability,
verifier, synthesizer) and the LangGraph state machine arrive in PLAN.md Phase 5.
"""

from orca_schemas import RunContext

SERVICE_NAME = "orca-agents"
__version__ = "0.1.0"


def new_run_context() -> RunContext:
    """Start a run owned by the agent service."""
    return RunContext(service=SERVICE_NAME)


__all__ = ["SERVICE_NAME", "__version__", "new_run_context"]
