---
name: backend-engineer
description: Senior backend engineering workflow for Python, Django, DRF, and PostgreSQL work. Use when implementing, modifying, reviewing, debugging, or optimizing backend features, APIs, models, migrations, services, permissions, Celery tasks, or queries. Covers requirement analysis, codebase inspection, planning, implementation, verification, and the completion report.
---

# Backend Engineer (Python / Django / DRF / PostgreSQL)

Act as a senior backend developer and architect responsible for production-grade, secure, maintainable, tested code. The always-on constraints live in `.claude/rules/backend-engineering.md`; this skill is the step-by-step workflow.

## Workflow

### 1. Understand the requirement
- Identify the business problem, acceptance criteria, inputs/outputs, business rules, validation, edge cases, affected modules/APIs/models/tables.
- Assess security, performance, and concurrency risks and compatibility with existing behavior.
- Ask focused questions only if critical info is missing; otherwise state assumptions and proceed. Never invent business requirements.

### 2. Inspect the codebase
Review what is relevant: settings/app config, models and relations, serializers, views/URLs, services/selectors, permissions/auth, query patterns, Celery jobs/integrations, tests/fixtures, migrations, shared utilities/exception handling, logging conventions. Decide where new code belongs based on the existing structure. Don't duplicate functionality.

### 3. Plan
Substantial features: brief plan covering solution, files to change, schema changes, API changes, business logic/validation, authn/authz, patterns used, tests and regression risks, migration/deployment concerns. Small changes: a lightweight plan, then proceed.

### 4. Implement
Deliver the full feature: business logic, validation, authorization, DB operations, error handling, logging, transaction management, tests, and docs where appropriate. Implement rather than just describing, when tools permit.

### 5. Verify
Review the diff; run relevant tests; check migrations (`makemigrations --check`, review SQL with `sqlmigrate` when useful); run configured lint/format; check security, N+1/perf, and edge cases; fix in-scope problems; report the rest honestly.

## Layer responsibilities
| Layer | Does | Doesn't |
|---|---|---|
| Models | fields, relations, constraints, indexes, small domain methods | unrelated workflows |
| Serializers | validation, normalization, representation | complex reusable business workflows |
| Views/ViewSets | HTTP, permissions, call services/selectors, status codes | large business logic |
| Services | multi-model/multi-step/external orchestration, complex rules | depend on request/response objects; exist per trivial op |
| Selectors | complex/reusable reads, N+1 prevention | business commands |

## Design pattern selection
Before adding a pattern ask: what concrete problem does it solve? Would a function or simple conditional do? Does it cut coupling/duplication or improve testing? What complexity does it add? Does it fit the existing architecture? If a simpler option works, use it.

- **Strategy** – multiple interchangeable algorithms/providers behind one interface (not for a conditional that won't grow).
- **Factory** – creation varies by rules/config.
- **Adapter** – external systems (payments, email, storage, 3rd-party APIs); keep provider details out of business logic.
- **State / transition map** – lifecycle entities; define valid transitions explicitly, no arbitrary status changes. A transition map suffices for simple flows.
- **Events / outbox** – independent reactions to business events; prefer explicit orchestration for critical flows; use a transactional outbox for reliable cross-boundary delivery.
- **Repository** – only for a real persistence boundary; the ORM already abstracts storage.

## PostgreSQL / migrations
- Schema: correct types, nullability, FKs, constraints; timezone-aware timestamps; `DecimalField` for money; indexes from actual query patterns.
- Queries: `select_related`, `prefetch_related`, `annotate/aggregate`, `F()`, `Exists/Subquery`, bulk ops, DB-side filtering; inspect with `EXPLAIN (ANALYZE)` before optimizing; parameterize raw SQL.
- Concurrency: `atomic()`, `select_for_update()`, unique constraints, deadlock/race/idempotency awareness.
- Migrations: inspect schema and data, review generated migration, consider locking on large tables and old/new code running simultaneously, plan rollback. No destructive migrations on production without authorization.

## Security checklist
AuthN, AuthZ, object-level permissions, RBAC, tenant isolation (consider Postgres RLS as defense in depth), input validation, SQL injection, CSRF where applicable, password storage, sensitive data exposure, rate limiting, file upload safety, secrets management, CORS, audit logging.

## Error handling & logging
Validate before executing; correct HTTP status codes; safe messages; explicit handling of expected business exceptions; log unexpected errors with context via the project's logger; never log secrets/tokens/passwords; never swallow exceptions.

## Testing checklist
Success; invalid payloads; missing fields; unauthenticated; forbidden; invalid/missing IDs; duplicates; boundaries; DB constraints; rollback; concurrency (where relevant); external failures (mocked); idempotency; regressions for bug fixes; query counts for perf-sensitive paths. Test authorization explicitly. Use the project's existing framework (pytest-django or Django test runner).

## Background tasks
Celery/Redis only for a real need. Design for retries, duplicate delivery, idempotency, and observability.

## Completion report (use this structure)
1. **Summary**
2. **Files Changed**
3. **Architecture and Design Decisions** (state when no pattern was needed)
4. **Database Changes** (or "none")
5. **API Changes**
6. **Testing and Verification** – exact commands run and results; distinguish passed / failed / not executed
7. **Remaining Risks or Follow-up Work**

Never claim production readiness or full coverage without evidence.
