# Repository Guidelines

## Project Structure & Module Organization
FastAPI entrypoint lives in `app.py`, delegating pricing integrations to `binance_client.py` and Polymarket parsing to `polymarket_parser.py`. Strategy math is isolated in `strategy_engine.py` for easy testing. Server-rendered HTML sits in `templates/` with `base.html` providing shared layout. Container assets and deployment helpers are in `Dockerfile` and `docker-compose.yml`, while dependency metadata is tracked in `pyproject.toml` and the lockfile `uv.lock`. Use `.env.example` as the starting point for local secrets such as `SESSION_SECRET`.

## Build, Test, and Development Commands
Install dependencies with `uv pip install --system .` after ensuring Python 3.11+. Launch the API locally via `uvicorn app:app --host 0.0.0.0 --port 5000 --reload` and visit `http://localhost:5000`. For containerized development, run `docker-compose up --build` to mirror production. Stop services using `docker-compose down` once finished.

## Coding Style & Naming Conventions
Follow PEP 8 with four-space indentation and descriptive snake_case for modules, functions, and variables (`extract_expiry_from_slug`). Keep business logic pure and side-effect free, matching `strategy_engine.py`. Maintain type hints and concise docstrings for new functions. Jinja templates should extend `base.html` and keep `hx-` attributes grouped near related form fields.

## Testing Guidelines
Pytest is the preferred framework; stage tests under `tests/` with filenames like `test_strategy_engine.py`. Target deterministic units first, e.g., the APY helpers in `strategy_engine.calculate_apy`. Run suites with `pytest` or narrow scope via `pytest tests/test_strategy_engine.py::test_calculate_apy`. Add fixtures for sample market payloads to avoid live HTTP calls, and treat external clients as mocked dependencies.

## Commit & Pull Request Guidelines
Write commit messages in the same imperative, present-tense format seen in history (`Add docker support`). Each pull request should summarize intent, link any relevant issues, and call out API or template changes. Include before/after screenshots whenever UI templates change. Confirm that linting, unit tests, and local runs (`uvicorn` or Docker) succeed before requesting review, and list any follow-up tasks explicitly in the PR body.

## Environment & Configuration Tips
Keep secrets out of version control; load overrides through a local `.env` file. Ensure a `static/` directory exists if you introduce new assets because `app.py` mounts it at `/static`. When deploying, update `docker-compose.yml` environment blocks to supply the same configuration values used locally.
