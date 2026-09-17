"""Model routing config (PLAN.md Phase 0.3 / §1.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from orca_api.config import DEFAULT_MODELS_CONFIG, ModelRouting, load_model_routing


def test_repo_routing_config_loads_and_has_required_roles() -> None:
    routing = load_model_routing(DEFAULT_MODELS_CONFIG)
    assert routing.version == 1
    for role in ModelRouting.REQUIRED_ROLES:
        assert role in routing.roles


def test_planner_and_verifier_use_the_documented_models() -> None:
    """PLAN.md §1.1 pins these; drift here is a documentation lie."""
    routing = load_model_routing(DEFAULT_MODELS_CONFIG)
    assert routing.role("planner").model == "claude-opus-5"
    assert routing.role("verifier").model == "claude-opus-5"
    assert routing.role("verifier").isolated_context is True
    assert routing.role("synthesizer").model == "claude-sonnet-5"
    assert routing.role("extractor").model == "claude-haiku-4-5"


def test_missing_required_role_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEFAULT_MODELS_CONFIG.read_text(encoding="utf-8"))
    del raw["roles"]["verifier"]
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required role"):
        load_model_routing(path)


def test_unknown_role_raises_with_available_names() -> None:
    routing = load_model_routing(DEFAULT_MODELS_CONFIG)
    with pytest.raises(KeyError, match="planner"):
        routing.role("does-not-exist")


def test_missing_config_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_model_routing(tmp_path / "nope.yaml")
