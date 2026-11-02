# Stem Robo Hub — Backend

Django + DRF + PostgreSQL API for the Stem Robo Hub storefront. Progress is tracked phase by phase in `docs/phases/`.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements/local.txt
cp .env.example .env          # set DJANGO_SECRET_KEY and DATABASE_URL
createdb robohub              # or any Postgres database named in DATABASE_URL
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```
Health check: `GET /health/`. API root: `/api/v1/`. Admin: `/admin/`.

## Commands
| Command | Purpose |
|---|---|
| `make run` / `make migrate` | Dev server / apply migrations |
| `make test` | pytest (uses `config.settings.test`) |
| `make lint` / `make format` | ruff |
| `make check` | `manage.py check` + pending-migration check |

## Layout
- `config/settings/{base,local,production,test}.py` — all environment values come from env vars.
- `config/api/versions.py` registers API versions; `config/api/v1.py` holds v1 routes (each feature app adds one entry). To add v2: create `config/api/v2.py` and register it.
- `apps/common/` — shared kernel (pagination envelope, error format, throttles, base model, health).
- Feature apps (added per phase) follow one shape: `models.py`, `services.py` (commands), `selectors.py` (reads), `api/` (views, serializers, urls), `admin.py`, `tests/`.
