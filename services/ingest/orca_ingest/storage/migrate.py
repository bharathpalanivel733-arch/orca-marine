"""Apply database migrations (PLAN.md Phase 1.10).

    python -m orca_ingest.storage.migrate      # or: pnpm db:migrate

Idempotent, so it is safe to run on every deploy and at service startup. Reads
``DATABASE_URL`` from the environment, defaulting to the local dev stack.
"""

from __future__ import annotations

import os
import sys

DEFAULT_DATABASE_URL = "postgresql://orca:orca@localhost:5432/orca"


def main() -> int:
    import psycopg

    from orca_ingest.storage import TimescaleObservationStore

    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        connection = psycopg.connect(url, connect_timeout=10)
    except psycopg.OperationalError as exc:
        print(f"cannot connect to {url.split('@')[-1]}: {exc}", file=sys.stderr)
        print("is the dev stack up? try: pnpm db:up", file=sys.stderr)
        return 1

    with connection:
        TimescaleObservationStore(connection).ensure_schema()
    print(f"migrations applied to {url.split('@')[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
