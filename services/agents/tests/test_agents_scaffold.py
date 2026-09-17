from __future__ import annotations

import orca_agents


def test_service_identity() -> None:
    assert orca_agents.SERVICE_NAME == "orca-agents"


def test_shared_contracts_are_wired() -> None:
    ctx = orca_agents.new_run_context()
    assert ctx.service == "orca-agents"
    assert ctx.run_id.startswith("orca_")
