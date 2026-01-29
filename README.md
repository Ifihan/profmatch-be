# ProfMatch

Professor-Student Research Matching API - An intelligent system that connects prospective postgraduate students with university professors whose research interests align with their academic background.

## Features

- **University Faculty Discovery**: Scrapes and extracts faculty information from any university website using LLM-powered extraction
- **Scholar Profile Enrichment**: Fetches publication data and citation metrics from Semantic Scholar
- **CV Parsing**: Extracts structured data from uploaded CVs (PDF, DOCX, TXT)
- **Intelligent Matching**: Uses Gemini to analyze research alignment and generate ranked recommendations
- **Profile Caching**: PostgreSQL-based caching to avoid redundant API calls

## Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL (profile cache), Redis (sessions)
- **LLM**: Google Gemini 2.5
- **MCP Servers**: Scholar, University, Document parsers

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL
- Redis
- Google Gemini API key

### Installation

```bash
# Clone and enter directory
cd profmatch

# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your values

# Run the server
uv run uvicorn app.main:app --reload
```

### Docker

```bash
docker build -t profmatch .
docker run -p 8000:8000 --env-file .env profmatch
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/session` | Create new session |
| GET | `/api/session/{id}` | Get session data |
| DELETE | `/api/session/{id}` | Delete session |
| POST | `/api/upload` | Upload CV (PDF/DOCX/TXT, max 10MB) |
| POST | `/api/match` | Start matching process |
| GET | `/api/match/{id}/status` | Check matching progress |
| GET | `/api/match/{id}/results` | Get ranked matches |
| GET | `/api/professor/{id}` | Get professor profile |
| GET | `/health` | Health check |

## Usage Flow

1. Create a session: `POST /api/session`
2. Upload CV: `POST /api/upload` with session_id and file
3. Start matching: `POST /api/match` with session_id, university URL, and research interests
4. Poll status: `GET /api/match/{id}/status?session_id=...`
5. Get results: `GET /api/match/{id}/results?session_id=...`

## Project Structure

```
profmatch/
├── app/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Settings
│   ├── models/              # Pydantic schemas
│   ├── routes/              # API endpoints
│   ├── services/            # Business logic
│   └── utils/               # Utilities
├── mcp-servers/
│   ├── scholar-server/      # Semantic Scholar integration
│   ├── university-server/   # University scraping
│   └── document-server/     # CV parsing
├── .env.example
├── Dockerfile
└── pyproject.toml
```

## Testing

```bash
# Install test dependencies
uv sync --extra test

# Run all tests
uv run pytest

# Run with coverage report
uv run pytest --cov=app --cov-report=term-missing

# Run only unit tests
uv run pytest tests/unit/

# Run only integration tests
uv run pytest tests/integration/

# Run specific test file
uv run pytest tests/unit/test_redis.py -v
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DEBUG` | Debug mode | `false` |
| `REDIS_URL` | Redis connection | `redis://localhost:6379` |
| `DATABASE_URL` | PostgreSQL connection | - |
| `GEMINI_API_KEY` | Google Gemini API key | - |
| `CORS_ORIGINS` | Allowed origins | `["http://localhost:3000"]` |
| `MAX_UPLOAD_SIZE_MB` | Max file size | `10` |
| `SESSION_TTL_HOURS` | Session expiry | `24` |

## License

MIT
