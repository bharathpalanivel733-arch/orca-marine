"""Health endpoint behaviour."""

from __future__ import annotations

from orca_schemas import HealthStatus, ServiceHealth


def test_healthz_is_ok_without_dependencies(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    health = ServiceHealth.model_validate(response.json())
    assert health.status is HealthStatus.OK
    assert health.service == "orca-api"


def test_readyz_reports_every_dependency(client) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    health = ServiceHealth.model_validate(response.json())
    assert {c.name for c in health.components} == {"postgres", "redis", "object-store"}
    # Status is whatever the environment really is; it must be a real rollup, never
    # an unconditional "ok".
    assert health.status in set(HealthStatus)
    if any(c.status is HealthStatus.DOWN for c in health.components):
        assert health.status is HealthStatus.DOWN


def test_unreachable_dependency_is_reported_down(client, monkeypatch) -> None:
    from orca_api.config import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", "postgresql://orca:orca@127.0.0.1:1/orca")
    try:
        health = ServiceHealth.model_validate(client.get("/readyz").json())
        postgres = next(c for c in health.components if c.name == "postgres")
        assert postgres.status is HealthStatus.DOWN
        assert postgres.detail
        assert health.status is HealthStatus.DOWN
    finally:
        get_settings.cache_clear()
        assert isinstance(get_settings(), Settings)
