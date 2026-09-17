"""Export Pydantic contracts to JSON Schema (PLAN.md Phase 0.1).

Two artifacts land in ``generated/``:

* one file per model, for any consumer that wants a single contract;
* ``contracts.json``, a bundle with every model under a shared ``$defs``, which is
  what the TypeScript generator compiles. Bundling matters: models share nested
  types (``ComponentHealth`` inside ``ServiceHealth``), and compiling the files
  independently would emit the same interface twice and produce invalid TypeScript.

Run via ``pnpm schemas:generate`` after changing any model in ``orca_schemas``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orca_schemas import ComponentHealth, ProblemDetail, RunContext, ServiceHealth
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

EXPORTED: tuple[type[BaseModel], ...] = (
    RunContext,
    ComponentHealth,
    ServiceHealth,
    ProblemDetail,
)

OUT_DIR = Path(__file__).parent / "generated"


def strip_property_titles(node: Any) -> None:
    """Drop Pydantic's auto-generated per-property ``title`` keys.

    json-schema-to-typescript promotes every titled sub-schema to its own exported
    alias, so a shared field name (``detail``) would collide across models.
    """
    if not isinstance(node, dict):
        return
    for container in ("properties", "$defs"):
        for subschema in node.get(container, {}).values():
            if isinstance(subschema, dict):
                subschema.pop("title", None)
                strip_property_titles(subschema)
    for key in ("anyOf", "allOf", "oneOf"):
        for subschema in node.get(key, []):
            strip_property_titles(subschema)


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(Path(__file__).parent)}")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    for model in EXPORTED:
        schema = model.model_json_schema()
        strip_property_titles(schema)
        schema["title"] = model.__name__
        _write(OUT_DIR / f"{model.__name__}.json", schema)

    _, bundle = models_json_schema(
        [(model, "validation") for model in EXPORTED],
        title="OrcaContracts",
    )
    strip_property_titles(bundle)
    for name, definition in bundle.get("$defs", {}).items():
        definition["title"] = name
    _write(OUT_DIR / "contracts.json", bundle)


if __name__ == "__main__":
    main()
