# Email & Notification Rules (Django / DRF / PostgreSQL)

Rules for every customer-facing email in this repo (`apps/notifications`). They extend
`backend-engineering.md`; where both speak, the stricter one wins. Use them when adding, changing or reviewing
an email, and when touching code that triggers one.

## Non-negotiables
1. **Email is a side effect, never part of the business transaction.** An order, payment, refund or webhook
   must succeed or fail on its own merits. A mail outage, a template bug or a bad address must never roll back,
   delay beyond a bounded timeout, or fail the request/webhook that triggered it.
2. **Send only after the data is committed.** Trigger with `notifications.after_commit(fn, id)`
   (`transaction.on_commit(..., robust=True)`), from inside the service that owns the state change. Never send
   from views, serializers, signals or model `save()`. An email about a rolled-back change is a lie to the customer.
3. **Pass ids, not objects, and rebuild from the database at send time.** The message reflects the state when it
   is sent (and when it is retried), not a stale snapshot. Never capture amounts, names or addresses in the trigger.
4. **Exactly once per business event.** Claim the event first: create the `EmailLog` row with a unique
   `dedupe_key` (`<kind>:<order_id>` or `<kind>:<payment_id>`), send only if the claim succeeded. Webhooks are
   delivered at least once, jobs retry, admins double-click: the claim is what makes all of that safe.
   Deliberate re-sends (staff "email invoice again") use no key and are always an explicit staff action.
5. **Senders never raise.** `notify_*` / `send_*` catch everything, log with `logger.exception`, and record the
   outcome on the `EmailLog` row (`failed`, short `error`). Callers must not need try/except.
6. **Nothing sensitive in logs or the log table.** Store kind, recipient, subject, status, attempts and a short
   error (`ExceptionType: message`, max 200 chars). Never store bodies, OTP codes, tokens, payment ids, full
   addresses or card/bank details. Never log a rendered message.
7. **Recipients come from the database.** Always `order.user.email` (verified at login). Never from request
   input, query strings or user-supplied fields on the event.
8. **Observable and recoverable.** Every send leaves a row staff can see (Admin > Email logs, and "Emails sent to
   the customer" on the order). Failures are retried by `manage.py retry_failed_emails` (max 3 attempts, then
   they stay `failed` for a human). Schedule that command next to `expire_payments`.

## Design
- **Where code lives:** triggers in the owning service (`checkout`, `payments.services`, `orders.services`);
  sending, templates, log model and retry in `apps/notifications`. `notifications` imports other apps lazily
  inside functions (orders and payments import it, so a top-level import back would be circular).
- **One event, one `Kind`.** To add an email: add a `Kind`, a `SUBJECTS` entry, `<kind>.html` + `<kind>.txt`,
  a `notify_*` function, one `after_commit` call at the state change, and tests (see checklist).
- **No Celery/queue until it is justified.** Today sends are synchronous after commit with `EMAIL_TIMEOUT`
  (10 s). If volume or latency demands it, move `deliver()` behind a task: the `EmailLog` claim, dedupe key and
  retry rules above already make that a drop-in change. Do not add a second code path before then.
- **No tax or business rules in templates.** Templates print values the services computed. Do not recompute
  totals, fees, refunds or dates in a template.

## Content
- Every email has an HTML part **and** a plain-text part (no HTML-only mail). Extend `base.html`; keep CSS
  inline (email clients strip `<style>`); tables for layout; max width 600px; works with images blocked.
- Autoescape stays on. Never mark user-supplied text (`ship_full_name`, address, product name) `|safe`.
- Subjects contain only business identifiers (order number, invoice number), never personal data or amounts.
- Say what happened, what the customer should do next (if anything), and how to reach us. Link to the order
  page with `SITE_URL`; never put tokens, one-time links or secrets in an email.
- Money is formatted from `Decimal` with `floatformat:2`, labelled in rupees. Dates use the project timezone.
- Refund wording must be true to the state: *started* = the gateway accepted it; *complete* = the gateway
  confirmed it (`refund.processed`). Never say "refunded" earlier, and never promise a bank timeline.
- Transactional only: no marketing in these emails. Marketing needs consent and an unsubscribe flow first.

## Configuration
- Switch: `NOTIFICATIONS_ENABLED` (default true). Turn it **off** in any environment that holds real customer
  addresses but must not mail them (staging copies, local dumps).
- Provider: standard Django `EMAIL_*` settings from the environment; local uses the console backend. Secrets
  only in env vars, never in git. `reply_to` is the seller email so customer replies reach a human.
- Seller identity in emails and invoices comes from `settings.SELLER` (`SELLER_*` env vars).

## Testing (required for every new email or trigger)
Use `django_capture_on_commit_callbacks(execute=True)` (pytest-django wraps tests in a transaction, so
`on_commit` hooks do not run on their own) and `django.core.mail.outbox`. Cover:
1. Sent to the right recipient with the right subject, order data, HTML **and** text part.
2. **Not** sent when the transaction rolls back or the business action is rejected.
3. Sent once when the trigger fires twice (replayed webhook, double call). Prove the dedupe test is real: remove
   the `dedupe_key` and watch the test fail.
4. A mail-server failure does not break the business action, is recorded as `failed`, and
   `retry_failed_emails` sends it later and stops at the attempt limit.
5. `NOTIFICATIONS_ENABLED=false` sends nothing and writes no log row.
6. Untrusted text is escaped in the HTML part.
7. Admin actions that send mail: POST-only, permission checked, nothing sent on GET or for unprivileged staff.

## Review checklist
- [ ] Triggered from a service via `after_commit`, with ids only?
- [ ] Claimed through `EmailLog` with a stable `dedupe_key` (or an explicit staff re-send)?
- [ ] Cannot raise into the caller; failure is logged and visible?
- [ ] No PII/secrets in logs, subject or log table?
- [ ] HTML + text parts, escaped, inline CSS, correct wording for the state?
- [ ] Tests listed above written, and the suite, `ruff check` and `makemigrations --check` pass?
