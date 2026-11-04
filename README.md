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
API docs (local): Swagger UI at `/api/docs/` (or `/api/v1/docs/`), ReDoc at `/api/v1/redoc/`, raw OpenAPI at `/api/v1/schema/`. Disabled in production unless `API_DOCS_ENABLED=true`. Every new endpoint must be annotated with `@extend_schema` (tags, request, responses incl. errors).

Health checks: `GET /api/v1/health` (enveloped, documented in Swagger, reports the database check; 503 when it fails) and `GET /health/` (plain `{"status": "ok"}` for load balancers). API root: `/api/v1/`. Admin: `/admin/`.

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

## Response format
Every JSON response from `/api/<version>/` (rendered by `apps.common.renderers.EnvelopeJSONRenderer`) uses one envelope; HTTP status codes are unchanged and `204` responses are empty.
```json
{ "success": true,  "data": { "...payload..." }, "error": null }
{ "success": false, "data": null, "error": { "message": "Please correct the highlighted fields.", "errors": { "email": ["Enter a valid email address."] } } }
```
Paginated lists keep `{items, page, pageSize, total, totalPages}` inside `data`. `/health/` is a plain probe and is not wrapped. Views and services return plain data or raise exceptions; never build the envelope by hand.

## Deploying on Render (free plan)
- Build Command `./build.sh`; Start Command `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 60`; `DATABASE_URL` = the Render Postgres **Internal** URL; `DJANGO_SETTINGS_MODULE=config.settings.production`.
- No shell on the free plan: set `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_NAME`, `DJANGO_SUPERUSER_MOBILE`, `DJANGO_SUPERUSER_PASSWORD` once; `build.sh` runs `manage.py ensure_superuser` (idempotent) to create the first admin. Delete the password variable afterwards.
- Alternatively run `createsuperuser` from your machine with `DATABASE_URL` set to the database's **External** URL.

## Online payments (Razorpay Payment Links)
`POST /api/v1/orders` with `"paymentMethod": "online"` holds the stock, creates a Razorpay payment link and returns
`{"redirectUrl": ...}`. The order is created only when Razorpay confirms the payment.

1. In the Razorpay dashboard (test mode first) create an API key and a webhook.
2. Webhook URL: `https://<your-api-host>/api/v1/payments/webhook`; events: `payment_link.paid`,
   `payment_link.cancelled`, `payment_link.expired`. Copy the webhook secret.
3. Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` (environment only, never in git).
   Without them online checkout answers 503 and `manage.py check --deploy` warns (`robohub.W003`).
4. The shopper returns to `{SITE_URL}/checkout/payment?reference=...`; the page polls
   `GET /api/v1/payments/<reference>` (`pending` / `success` / `failed`).
5. Unpaid links expire after `PAYMENT_LINK_EXPIRY_MINUTES` (30). Run `python manage.py expire_payments` on a
   schedule (e.g. every 15 minutes) as a safety net to release held stock. Render free has no cron, so rely on
   Razorpay's `payment_link.expired` webhook there, or call it from an external scheduler.
6. Payments taken but not fulfillable (amount mismatch, stock gone after a late payment) show up in the admin under
   Payment attempts with status "needs refund"; refund them in the Razorpay dashboard.
