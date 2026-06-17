#!/usr/bin/env sh
# Preview entrypoint: one container runs migrations, the ARQ worker, and the API.
# Cloud Run service is pinned to a single always-on instance (min=max=1, CPU
# always allocated) so the background worker keeps consuming jobs.
set -e

alembic upgrade head || echo "alembic upgrade failed — continuing on existing schema"

arq app.workers.worker.WorkerSettings &

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"