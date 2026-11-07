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

## Blog
`GET /api/v1/articles` (newest first, optional `?limit=` and `?category=<slug>`), `GET /api/v1/articles/{slug}` (adds
`content`, the body as sanitized HTML) and `GET /api/v1/article-categories` (active categories that have a published
article, with counts) are public. An article's `media` is an image or a video (`mediaType`); `image` is kept for older
clients. Categories are a master list (Admin > Content > Article categories) that articles pick from. Staff write articles in Admin > Content > Articles with the rich-text editor; an article is
public only while "Is published" is on and its publish time has arrived (leave the time empty to publish now, or set a
future time to schedule). `python manage.py seed_articles` adds three sample articles for local development.

## Customer emails
`apps/notifications` emails the customer on four events, after the database commit and at most once each
(`EmailLog` has a unique `dedupe_key`; see Admin > Email logs). The status buttons in the admin (Accept,
Ship, Deliver, Cancel) go through the same services, so they send these emails too:

| Event | Email |
|---|---|
| An order is real: placed with nothing due online, or its online payment arrived | Order confirmation |
| An order ships (`transition` to `shipped`) | Order shipped (delivery partner and tracking number, or "details will follow") |
| Staff assign or correct the delivery partner / tracking number on an order (each distinct value once; not for cancelled orders) | Delivery details |
| An order ships | Invoice: the full invoice in the email body, plus a printable HTML copy attached; staff can also resend it from the order page or the orders list |
| An order is delivered (cash collected for COD) | Order delivered (thank-you, summary, payment confirmation) |
| A paid order is cancelled and the gateway accepts the refund | Refund started |
| Razorpay confirms the refund (`refund.processed`) | Refund complete |

Mail is sent with Django's SMTP backend: set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and
`EMAIL_USE_TLS` (or `EMAIL_USE_SSL`) in the environment, then check it with
`python manage.py send_test_email you@example.com`. With no `EMAIL_HOST` mail is printed to the console.

A mail failure never affects the order: the row is marked `failed` and `python manage.py retry_failed_emails`
(schedule it with `expire_payments`, e.g. every 15 minutes) sends it again, up to 3 attempts. Set
`NOTIFICATIONS_ENABLED=false` to send nothing. Local development prints emails to the console. Rules for adding
or changing an email: `.claude/rules/email-notifications.md`.

## Orders, payments and the COD transit fee
The order is created first, at checkout, and payments hang off it (`Order` -> `Payment` -> `PaymentTransaction`
ledger; `Order.amount_paid` is the net of the ledger). Every status change goes through
`orders.services.transition` and leaves an `OrderStatusEvent` row.

- **Online** (`paymentMethod: "online"`): the order is `awaiting_payment` and the response carries `reference` plus
  `redirectUrl` (`paymentFlow: "link"`, Payment Link) or `razorpay` (`checkout`, the default, for Razorpay Checkout).
- **COD**: the item price is paid in cash on delivery; a **transit fee** is paid online first. The fee comes from the
  `CodTransitPolicy` table (admin > Orders > Cod transit policies): fixed or percent, per default / state / pincode
  prefix, with optional order-value bands; the most specific rule wins and no rule means no fee. With a fee the order
  is `awaiting_payment`, without one it is `pending` at once. The amount is frozen on the order (`OrderCharge`).
- `POST /checkout/quote` prices an order without creating it; `POST /orders/{id}/pay` pays an unpaid order again.
- Unpaid orders are visible in the history (`awaiting_payment`, with `payment` to resume). They expire after
  `ORDER_PAYMENT_DUE_MINUTES` (60) and give their stock back.
- **Refunds**: cancelling before dispatch refunds everything paid online (including the COD fee). The order
  shows `paymentStatus: "refund_in_progress"` until Razorpay confirms the refund (`refund.processed` webhook, or
  `expire_payments`, which also checks refunds still in progress), then `refunded`. If the customer
  refuses delivery (`rto`) the fee is kept. A refund that fails is logged and the payment stays `captured` for staff.

### Razorpay setup
1. In the Razorpay dashboard (test mode first) create an API key and a webhook.
2. Webhook URL `https://<your-api-host>/api/v1/payments/webhook`; events: `payment_link.paid`,
   `payment_link.cancelled`, `payment_link.expired`, `payment.authorized`, `payment.captured`, `order.paid`, `refund.processed`, `refund.failed`. Copy the
   webhook secret.
3. Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` (environment only, never in git).
   Without them any order that needs an online payment answers 503 and `manage.py check --deploy` warns
   (`robohub.W003`).
4. Link flow: the shopper returns to `{SITE_URL}/checkout/payment?reference=...`; the page polls
   `GET /api/v1/payments/<reference>` (`pending` / `success` / `failed`). Checkout flow: call
   `POST /api/v1/payments/verify` from Checkout's success handler.
5. Run `python manage.py expire_payments` on a schedule (e.g. every 15 minutes) so unpaid orders expire even if no
   webhook arrives. Render free has no cron: use an external scheduler.
6. Money that arrived but cannot be used (wrong amount, order already cancelled, stock gone after a late payment)
   shows under Payments with status "needs refund"; refund it in the Razorpay dashboard.
7. Upgrading from the previous payment model: `payments/0003` refuses to run while any attempt is still pending or
   needs a refund. Run `expire_payments`, refund those, then migrate.
