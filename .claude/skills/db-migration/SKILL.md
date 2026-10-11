---
name: db-migration
description: Change the database schema in apps/api. Use when adding, altering, or removing a SQLAlchemy model field, table, or constraint.
---

# DB migration

## Steps

1. **Model first.** Change the SQLAlchemy model in `models` to match the new shape.
2. **Generate.** Run `alembic revision --autogenerate -m "<description>"`. Never hand-write a migration from scratch when autogenerate can produce it.
3. **Review the generated SQL by hand.** Autogenerate gets the happy path right but not everything — check specifically for:
   - An index on `business_id` (and on any other column this table will be filtered or joined by).
   - `created_at` / `updated_at` present and defaulted where the table needs them.
   - Foreign keys pointing at the right table, with the right `ON DELETE` behavior.
   - Unique constraints that should exist (e.g., one active calendar connection per business) actually generated.
4. **Write and test the downgrade.** `alembic downgrade -1` must actually run, not just exist as an empty stub. If the upgrade is destructive (dropping a column with data), write the backfill or data-preserving path before writing the downgrade.
5. **Never run migrations on app startup.** They run as an explicit step in deploy/CI, not inside the FastAPI app factory.

## Checklist

- [ ] Model and migration match.
- [ ] Indexes on `business_id` and other filter/join columns.
- [ ] Timestamps, FKs, and unique constraints reviewed by hand, not just trusted from autogenerate.
- [ ] Downgrade tested, not just present.
- [ ] Migration is not invoked from app startup code.
