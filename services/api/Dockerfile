# ORCA API (PLAN.md Phase 0.1)
# Python is pinned to 3.12 rather than the host's 3.14: the Phase 1 science stack
# (xarray / GeoPandas / Shapely / psycopg) has reliable wheels there.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY packages/schemas /app/packages/schemas
COPY services/api /app/services/api

RUN pip install --no-cache-dir ./packages/schemas ./services/api

EXPOSE 8000
CMD ["uvicorn", "orca_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
