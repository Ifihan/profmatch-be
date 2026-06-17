# ProfMatch — Backend

Professor–student research matching platform. FastAPI + Gemini, async job pipeline.

## Quick start
```bash
cp .env.example .env          # fill in GEMINI_API_KEY etc.
docker compose up -d db redis
pip install -e ".[dev]"
# run migrations (see alembic_setup.md), then:
uvicorn app.main:app --reload          # API
arq app.workers.worker.WorkerSettings  # worker (separate terminal)
```

## Architecture
- `app/api/routes/` — auth, matches, admin endpoints
- `app/services/credits.py` — append-only credit ledger (balance derived, lazy regen)
- `app/services/pipeline/` — the 5 match stages (plain async, no agent/MCP)
- `app/services/enrichment/` — OpenAlex (primary) + Semantic Scholar/Crossref fallback
- `app/workers/worker.py` — runs the pipeline, checkpoints each stage to the job row

## Why the job is async
`POST /matches` returns a `job_id` immediately and the worker runs independently.
A page refresh re-attaches via `GET /matches/{job_id}` — the search never restarts.
Each stage persists its output, so even a worker crash resumes mid-pipeline.

## Build order
See the TRD §10. Short version: scaffold → auth → credit ledger → job infra
(with a stub worker) → pipeline stages → admin → hardening → payments.
