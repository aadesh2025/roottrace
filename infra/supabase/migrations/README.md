# Migrations

Empty until **T1.2**, which writes all **15** of them in the order fixed by
`docs/04-DATA-MODEL.md` §15. That ordering is load-bearing, not stylistic:

| Constraint | Why |
|---|---|
| `…000900_auth_helpers` before `…001000_rls_policies` | Every policy references `rt_auth.*` |
| `…001200_partition_security` before any partition is created | `secure_partition()` must exist when the first partition is made |
| `…001300_materialized_views` after `…000900_auth_helpers` | Accessors call `rt_auth.project_ids()` |
| **`…001500_rls_assertions` LAST** | It is the gate. Earlier, it would fire on tables that are legitimately not yet secured |

Rules:

- **Forward-only.** A mistake is corrected by a new migration, never by editing
  an applied one.
- Idempotent (`if not exists` / `or replace`) so re-runs are safe.
- Destructive changes are two-phase: deploy code tolerating both shapes,
  migrate, then remove the tolerance.
- `create index concurrently` on large tables.

## The partition trap (B13)

Partitions are separate relations and **inherit neither RLS nor policies**.
`select * from raw_events_2026_08` consults only that partition's own policies —
the parent's are never reached — and `authenticated` holds `SELECT` on it
through Supabase's schema-wide grants. Every partition is therefore secured at
creation by `rt_admin.secure_partition()`, and `ensure_partitions()` creates and
secures in a single function so the monthly job cannot reopen the gap.

The §12.9 coverage assertion deliberately includes partitions (`relkind in
('r','p')`). If it fires, it is working. **Do not silence it by excluding
partitions** — that hides the hole instead of closing it.
