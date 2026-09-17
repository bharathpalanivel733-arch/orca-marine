#!/usr/bin/env bash
# ORCA — dev stack verification (PLAN.md Phase 0.2 acceptance check).
#
# Proves the three things the plan actually requires of the database — PostGIS,
# TimescaleDB and pgvector present in ONE database — plus Redis and the object store.
# Uses `docker compose exec`, so no psql/redis-cli is needed on the host.
#
# Usage: pnpm db:verify   (or: bash scripts/verify-stack.sh)
set -uo pipefail

COMPOSE="docker compose -f infra/docker-compose.yml"
failures=0

step() { printf '\n== %s ==\n' "$1"; }
ok()   { printf '  OK    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }

step "Docker daemon"
if ! docker info >/dev/null 2>&1; then
  fail "docker daemon is not responding — start Docker Desktop and retry"
  exit 1
fi
ok "daemon responding"

step "Containers"
$COMPOSE ps

step "PostgreSQL extensions"
for ext in postgis timescaledb vector pg_trgm; do
  version=$($COMPOSE exec -T db psql -U orca -d orca -tAc \
    "SELECT extversion FROM pg_extension WHERE extname = '${ext}'" 2>/dev/null | tr -d '\r')
  if [ -n "$version" ]; then ok "${ext} ${version}"; else fail "${ext} not installed"; fi
done

step "Schemas"
for schema in evidence geo provenance; do
  found=$($COMPOSE exec -T db psql -U orca -d orca -tAc \
    "SELECT 1 FROM information_schema.schemata WHERE schema_name = '${schema}'" 2>/dev/null | tr -d '\r')
  if [ "$found" = "1" ]; then ok "schema ${schema}"; else fail "schema ${schema} missing"; fi
done

step "PostGIS is functional"
dist=$($COMPOSE exec -T db psql -U orca -d orca -tAc \
  "SELECT round(ST_Distance('SRID=4326;POINT(80.3 13.1)'::geography, 'SRID=4326;POINT(80.4 13.1)'::geography))" 2>/dev/null | tr -d '\r')
if [ -n "$dist" ]; then ok "ST_Distance on geography returned ${dist} m"; else fail "ST_Distance query failed"; fi

step "Redis"
if [ "$($COMPOSE exec -T redis redis-cli ping 2>/dev/null | tr -d '\r')" = "PONG" ]; then
  ok "PONG"
else
  fail "redis did not reply to PING"
fi

step "Object store"
if curl -fsS -o /dev/null http://localhost:9000/minio/health/live; then
  ok "minio live"
else
  fail "minio health endpoint unreachable on :9000"
fi

printf '\n'
if [ "$failures" -eq 0 ]; then
  echo "stack verified: all checks passed"
else
  echo "stack verification FAILED: ${failures} check(s)"
fi
exit "$failures"
