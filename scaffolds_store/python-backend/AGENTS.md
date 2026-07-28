# Python Backend Conventions

## Project Structure
- `src/` — application package
- `tests/` — test suite, mirrors `src/` layout
- `docs/` — documentation
- `pyproject.toml` — project metadata and tool config

## Code Style
- `from __future__ import annotations` in every file
- Type hints on all public functions and methods
- Ruff for linting (select E, F, I, N, W), line-length 100
- Target Python 3.11+

## Testing
- pytest with `testpaths = ["tests"]`
- Fixtures in `conftest.py` at appropriate scope levels
- Test files named `test_*.py`, functions named `test_*`
- Prefer `tmp_path` fixture over manual temp dirs
- Use `pytest-cov` for coverage reports

## API Design (FastAPI)
- Route prefixes under `/api/v1/`
- Pydantic models for request/response schemas
- Dependency injection for auth, DB sessions
- Use `HTTPException` for error responses
- Keep controllers thin; business logic in service modules

## Error Handling
- Define domain-specific exception classes
- Global exception handlers map exceptions to HTTP responses
- Never bare `except:` — catch specific exceptions
- Log via `logger.exception(...)` in exception handlers

## Database Access
- Async drivers (asyncpg) for production
- Alembic for schema migrations
- Repository pattern for data access abstraction
- Never raw SQL in views — use repositories

## Dependencies
- `pyproject.toml` for all dependency declarations
- Dev dependencies in `[project.optional-dependencies] dev`
- Pin minimum versions, prefer loose upper bounds
