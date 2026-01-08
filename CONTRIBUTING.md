# Contributing to ProfMatch

Thank you for your interest in contributing to ProfMatch! This guide will help you get started.

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Redis (for session storage)
- PostgreSQL (optional, for caching)

### Environment Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/profmatch.git
   cd profmatch
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   uv venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   uv sync
   ```

3. Copy the environment template and configure:
   ```bash
   cp .env.example .env
   ```

4. Configure required environment variables in `.env`:
   - `GEMINI_API_KEY` - Google Gemini API key
   - `REDIS_URL` - Redis connection URL
   - `SUPABASE_URL` and `SUPABASE_KEY` (optional) - For database caching

### Running Locally

Start the development server:
```bash
uv run uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

## Project Structure

```
profmatch/
├── app/
│   ├── main.py              # FastAPI application entry point
│   ├── models/              # Pydantic models and schemas
│   ├── routers/             # API route handlers
│   ├── services/            # Business logic
│   │   ├── orchestrator.py  # Main matching pipeline
│   │   ├── mcp_client.py    # MCP server communication
│   │   ├── gemini.py        # LLM integration
│   │   ├── cache.py         # Caching logic
│   │   └── redis.py         # Session management
│   └── utils/               # Helper utilities
├── mcp-servers/             # MCP server implementations
│   ├── scholar-server/      # Semantic Scholar API integration
│   └── document-server/     # Document parsing (CV/resume)
└── tests/                   # Test files
```

## Code Style

- Follow [PEP 8](https://pep8.org/) style guidelines
- Use type hints for function signatures
- Keep functions focused and single-purpose
- Write docstrings for public functions and classes

### Formatting

We recommend using `ruff` for linting and formatting:
```bash
uv run ruff check .
uv run ruff format .
```

## Making Changes

### Branching

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes with clear, atomic commits

3. Push and open a pull request

### Commit Messages

Use clear, descriptive commit messages:
- `feat: add new endpoint for batch matching`
- `fix: resolve rate limiting issue in scholar server`
- `docs: update API documentation`
- `refactor: simplify affiliation verification logic`

### Pull Requests

- Provide a clear description of changes
- Reference any related issues
- Ensure all tests pass
- Keep PRs focused on a single feature or fix

## Testing

Run the test suite:
```bash
uv run pytest
```

Run with coverage:
```bash
uv run pytest --cov=app
```

## MCP Servers

The project uses Model Context Protocol (MCP) servers for external data integration.

### Scholar Server

Located in `mcp-servers/scholar-server/`, this server interfaces with the Semantic Scholar API.

To test the scholar server independently:
```bash
cd mcp-servers/scholar-server
uv run python server.py
```

### Adding New MCP Servers

1. Create a new directory under `mcp-servers/`
2. Implement the server following the MCP protocol
3. Add client methods in `app/services/mcp_client.py`
4. Update the orchestrator to use the new data source

## API Documentation

Once the server is running, access the interactive API docs at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Getting Help

- Open an issue for bugs or feature requests
- Check existing issues before creating new ones
- Provide detailed reproduction steps for bugs

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.