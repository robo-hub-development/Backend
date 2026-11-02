# Backend Engineering Rules (Python / Django / DRF / PostgreSQL)

Always-on rules for all backend work in this repo. For the full workflow, checklists, and completion report format, use the `backend-engineer` skill.

## Non-negotiables
1. Understand the requirement before implementing; ask focused questions only for critical gaps, otherwise state assumptions and proceed. Never invent or silently change business rules.
2. Inspect existing code (settings, models, serializers, views, services, permissions, tests, migrations) before modifying; follow established architecture unless there is a clear technical reason not to.
3. Prefer the simplest solution (KISS/YAGNI). Apply SOLID/design patterns only when they solve a real problem. No speculative abstractions, no new dependencies without need.
4. Keep business logic out of views/serializers; use a service layer for multi-model/multi-step workflows, selectors/query functions for complex reads. No service class per trivial operation.
5. Protect data integrity in the database (FKs, unique/check constraints, correct `on_delete`, `DecimalField` for money, indexes based on real query patterns).
6. Use `transaction.atomic()` for multi-step writes; consider `select_for_update()`, race conditions, and idempotency for critical flows (payments, orders, inventory, approvals). External calls are not rolled back by DB transactions.
7. Enforce authN/authZ on the backend, including object-level permissions and tenant isolation. Never trust user-supplied IDs. Never hardcode secrets or expose stack traces/tokens.
8. Preserve API and data backward compatibility; follow the existing response format. Flag and justify breaking changes.
9. Write and run tests for new logic and bug fixes (success, invalid input, authn/authz failures, missing/duplicate records, constraints, rollback). Use the project's existing test framework.
10. Never claim code changed, commands ran, or tests passed unless they actually did. Report failures and unrun checks honestly. Investigate root causes; don't retry blindly.
11. No destructive DB operations, production changes, or deployments without explicit authorization. Don't modify unrelated code or overwrite user changes.

## Python
- PEP 8 + project formatter/linter; type hints where they aid clarity; small focused functions; no mutable default args; narrow exception handling; no silent swallowing of errors.
- Comments explain *why*, not *what*.

## Django / DRF
- Models: domain data, constraints, focused methods. Always create and review migrations; consider locking on large tables and staged changes.
- Serializers: validation, normalization, representation only.
- Views/ViewSets: HTTP concerns, permissions, call services/selectors, consistent status codes. Paginate large lists.
- Prevent N+1 (`select_related`/`prefetch_related`), push filtering/aggregation to the DB, parameterize any raw SQL, check `EXPLAIN` before optimizing.
- Signals sparingly; prefer explicit service orchestration for critical flows. Celery tasks must tolerate retries and duplicate delivery.

## Pattern guidance (use only when justified)
Service Layer, Strategy (multiple interchangeable rules/providers), Factory (varying creation), Adapter (external systems), State/transition map (lifecycles), Events/outbox (reliable async reactions). Repository only for a real persistence boundary — the ORM is already an abstraction.

## Decision order
Correctness → Security → Data integrity → Simplicity → Maintainability → Testability → Performance → Extensibility → Operability.
