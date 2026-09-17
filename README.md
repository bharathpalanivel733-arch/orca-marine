# ORCA — Marine EcOsystem Reasoning with Collaborative Agents

Team **OCEAN-IQ** · SIH **26176** · Theme: Disaster Management

ORCA is the agentic reasoning and trust layer on top of INCOIS, IMD and ISRO marine
feeds — not a new data source or a competing app. See `docs/SPEC.md` for the problem
statement and `docs/PLAN.md` for the phased execution plan (the source of truth for
what gets built, and in what order).

> **Status: Phase 0 (foundations) only.** No marine data source is connected, and the
> system produces no advisory, safety score, geofence verdict or route. Those arrive in
> Phases 1–4.

## Layout

| Path | What it is |
|---|---|
| `apps/web` | Next.js 15 + Tailwind v4 + shadcn/ui front end (currently a dev-stack status page) |
| `services/api` | FastAPI orchestration API: config, observability, health |
| `services/agents` | Agent mesh package (scaffold; roster lands in Phase 5) |
| `services/ingest` | Data ingestion package (scaffold; adapters land in Phase 1) |
| `packages/schemas` | Pydantic contracts, exported to JSON Schema and TypeScript |
| `infra` | Docker Compose dev stack and database bootstrap |
| `config/models.yaml` | LLM routing table — which model serves which role |
| `docs` | Spec, architecture, methods, claims, deployment, demo, plan, progress |

## Prerequisites

Node 20+ with pnpm, Python 3.12+, and Docker Desktop (for the database stack).

## Setup

```bash
pnpm install          # JS workspace
pnpm setup:py         # .venv + editable installs of every Python package
```

Activate the virtualenv before running the Python-backed scripts:

```bash
source .venv/Scripts/activate    # Windows (Git Bash);  .venv/bin/activate elsewhere
```

Copy `.env.example` to `.env` and fill in what you have. Every credential may be left
blank — services degrade and report it rather than crashing.

## Running

```bash
pnpm db:up        # start Postgres(+PostGIS/TimescaleDB/pgvector), Redis, MinIO
pnpm db:verify    # prove the extensions and schemas really exist
pnpm api:dev      # FastAPI on :8000
pnpm dev          # dev stack + web app on :3000
```

`make` targets (`make dev`, `make verify-stack`, …) forward to the same scripts for
anyone who has make installed; the pnpm scripts are canonical.

## Checks

```bash
pnpm lint         # eslint + ruff
pnpm typecheck    # tsc + mypy (strict)
pnpm test         # pytest
pnpm verify       # all three
```

## Contracts

`packages/schemas/orca_schemas` is the single source of truth for shared types. After
changing a model, run `pnpm schemas:generate` to refresh the JSON Schema and the
TypeScript types the web app imports.

## Non-negotiable rule

Safety scores, geofence verdicts, route costs and every other number come from
deterministic kernels, classical statistics or PostGIS — never from an LLM. Language
models handle language, planning, critique and narration over numbers the kernels
already computed (`docs/PLAN.md` §1, `docs/METHODS.md` §1).
