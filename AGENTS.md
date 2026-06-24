# AGENTS.md

## Cursor Cloud specific instructions

### Overview
AgendaOps MVP ("Karargah") is a Python/FastAPI OSINT political intelligence platform. It ingests data from RSS, YouTube, and Twitter/X, analyzes content with Google Gemini AI, and generates opportunity cards. See `README.md` for basic setup.

### Required Services
| Service | How to start | Port |
|---|---|---|
| PostgreSQL 15 | `sudo docker start agendaops-postgres` (or `sudo docker run -d --name agendaops-postgres -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=agendaops -p 5432:5432 postgres:15-alpine`) | 5432 |
| Redis 7 | `sudo docker start agendaops-redis` (or `sudo docker run -d --name agendaops-redis -p 6379:6379 redis:7-alpine`) | 6379 |
| FastAPI (dev) | `PYTHONPATH=/workspace DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/agendaops" REDIS_URL="redis://localhost:6379/0" uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` | 8000 |

### Important Gotchas
- **Environment variables must override Docker hostnames**: The `.env` file defaults DB host to `db` and Redis to `redis` (Docker Compose service names). When running locally outside Docker Compose, you **must** export `DATABASE_URL` and `REDIS_URL` pointing to `localhost` (or set them in `.env`). The `session.py` fallback default also uses `db:5432`.
- **DB initialization**: After starting the server, hit `GET /api/init-db` to create all tables. Alembic migrations also exist but the init-db endpoint is simpler for dev.
- **Tests**: Run `PYTHONPATH=/workspace python3 -m pytest app/tests/ -v`. Tests use in-memory SQLite (via `aiosqlite`), so no DB/Redis needed. Note: `confest.py` (sic) is misspelled — pytest does not auto-discover it as `conftest.py`, so the async fixtures in it are not used by `test_api.py`. The `test_read_root` test has a pre-existing failure (expects JSON from `/` but the route redirects to `/dashboard` HTML).
- **`aiosqlite` is a test-only dependency** not listed in `requirements.txt` — install it alongside the main deps.
- **Gemini/YouTube/RapidAPI keys are optional**: The app degrades gracefully without them. External API features will return errors or empty results, but the core platform (RSS ingestion, dashboard, CRUD) works fine.
- **Python PATH**: Scripts installed via pip go to `/home/ubuntu/.local/bin`. Make sure this is on `PATH`.
- **Docker daemon**: Must be started with `sudo nohup dockerd > /tmp/dockerd.log 2>&1 &` before starting containers. Requires fuse-overlayfs and iptables-legacy configuration (see environment setup).
