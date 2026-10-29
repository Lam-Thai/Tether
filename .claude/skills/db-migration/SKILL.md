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
   - An index on every FK column with `ON DELETE SET NULL` / `CASCADE` (autogenerate omits it; deletes then scan the table).
   - A uniqueness rule over a nullable column ("one global row where `business_id IS NULL`") needs partial unique indexes, and every `ON CONFLICT` against them needs the matching `index_where` (see `models/usage.py`, `repositories/usage.py`).
   - A value list enforced by a check constraint (e.g. usage kinds) changes only through a migration that rewrites the constraint.
   - Autogenerate ignores a changed `server_default` (it produced an empty migration for `agents.active` -> false in slice 16): write that `alter_column(..., server_default=...)` by hand, change the model's Python `default` too, and leave existing rows alone unless the issue says otherwise.
   - "No two rows may overlap in time" is an `EXCLUDE USING gist` constraint (see `ex_bookings_no_overlap` in `models/booking.py`), not an application check: it needs `CREATE EXTENSION IF NOT EXISTS btree_gist` in the upgrade (left installed on downgrade), and autogenerate emits the `ExcludeConstraint` from the model. Repositories catch the `IntegrityError` and match the constraint name in `str(exc.orig)` to return a value (`SlotConflict`) instead of raising, inside a `begin_nested()` so the session stays usable. Concurrent inserts racing on an exclusion constraint can deadlock (40P01), so serialize them per scope with a `pg_advisory_xact_lock` taken right before the insert and released by the commit that follows (`repositories/bookings.lock_reservations`); never hold it across a provider call.
4. **Write and test the downgrade.** `alembic downgrade -1` must actually run, not just exist as an empty stub. If the upgrade is destructive (dropping a column with data), write the backfill or data-preserving path before writing the downgrade.
5. **Never run migrations on app startup.** They run as an explicit step in deploy/CI, not inside the FastAPI app factory.

## Checklist

- [ ] Model and migration match.
- [ ] Indexes on `business_id` and other filter/join columns.
- [ ] Timestamps, FKs, and unique constraints reviewed by hand, not just trusted from autogenerate.
- [ ] Downgrade tested, not just present.
- [ ] Migration is not invoked from app startup code.
