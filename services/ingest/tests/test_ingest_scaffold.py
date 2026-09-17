from __future__ import annotations

import orca_ingest


def test_service_identity() -> None:
    assert orca_ingest.SERVICE_NAME == "orca-ingest"


def test_shared_contracts_are_wired() -> None:
    ctx = orca_ingest.new_run_context()
    assert ctx.service == "orca-ingest"
    assert ctx.run_id.startswith("orca_")
