"""Configuration and LLM routing (PLAN.md Phase 0.3).

Two sources, deliberately separate:

* environment variables -> infrastructure endpoints and credentials (``Settings``)
* ``config/models.yaml``  -> which model serves which role (``ModelRouting``)

Model choice is config, never code, so swapping a model is a one-line edit
(PLAN.md §1.1). Nothing here calls a provider; Phase 0 loads and validates only.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODELS_CONFIG = REPO_ROOT / "config" / "models.yaml"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    """Environment-driven settings. See ``.env.example`` for the full template."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    orca_env: str = "development"
    orca_log_level: str = "INFO"
    orca_log_format: LogFormat = LogFormat.JSON

    database_url: str = "postgresql://orca:orca@localhost:5432/orca"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "orca-cache"

    # Bhashini ULCA credentials (PLAN.md Phase 7.2, 7.6). Absent by default, and the
    # speech chains treat absence as "provider unavailable" rather than as an error — the
    # fallback hierarchy and the pre-generated audio cache carry the demo without them.
    bhashini_ulca_api_key: str = ""
    bhashini_user_id: str = ""
    speech_audio_bucket: str = "orca-audio"

    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "orca-api"

    models_config_path: Path = DEFAULT_MODELS_CONFIG


class RoleConfig(BaseModel):
    """One LLM role binding from ``config/models.yaml``."""

    model_config = ConfigDict(extra="forbid")

    description: str
    provider: str
    model: str
    thinking: str | None = None
    effort: str | None = None
    max_tokens: int = Field(gt=0)
    structured_output: bool = False
    isolated_context: bool = False


class QueryBudget(BaseModel):
    """Planner cost budget (PLAN.md Phase 5.3). Recorded now, enforced in Phase 5."""

    model_config = ConfigDict(extra="forbid")

    max_total_tokens: int = Field(gt=0)
    max_wall_clock_seconds: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)


class ModelRouting(BaseModel):
    """Validated contents of ``config/models.yaml``."""

    model_config = ConfigDict(extra="forbid")

    version: int
    defaults: dict[str, Any] = Field(default_factory=dict)
    roles: dict[str, RoleConfig]
    fallbacks: dict[str, Any] = Field(default_factory=dict)
    budgets: dict[str, QueryBudget] = Field(default_factory=dict)

    REQUIRED_ROLES: ClassVar[tuple[str, ...]] = ("planner", "verifier", "synthesizer", "extractor")

    def role(self, name: str) -> RoleConfig:
        """Return one role binding, or raise with the available names."""
        try:
            return self.roles[name]
        except KeyError:
            available = ", ".join(sorted(self.roles))
            msg = f"unknown LLM role {name!r}; configured roles: {available}"
            raise KeyError(msg) from None


def load_model_routing(path: Path | None = None) -> ModelRouting:
    """Load and validate the routing table.

    Raises ``FileNotFoundError`` if absent and ``ValueError`` if a role required by
    PLAN.md §1.1 is missing, so a misconfiguration fails at startup rather than
    halfway through a query.
    """
    config_path = path or get_settings().models_config_path
    if not config_path.is_file():
        msg = f"model routing config not found at {config_path}"
        raise FileNotFoundError(msg)

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    routing = ModelRouting.model_validate(raw)

    missing = [r for r in ModelRouting.REQUIRED_ROLES if r not in routing.roles]
    if missing:
        msg = f"model routing is missing required role(s): {', '.join(missing)}"
        raise ValueError(msg)
    return routing


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, read once."""
    return Settings()
