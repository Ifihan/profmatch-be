#!/usr/bin/env sh
set -e

alembic upgrade head || echo "alembic upgrade failed — continuing on existing schema"

if [ "${QUEUE_BACKEND}" != "cloudtasks" ]; then
    arq app.workers.worker.WorkerSettings &
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"