# Supabase → OVH PostgreSQL Migration Plan

Migrate ExplainIT from Supabase PostgreSQL to PostgreSQL 17 on OVH using logical replication, a short write pause, validation, and a defined rollback window.

**Current status:** Phase 1 source assessment complete. Target locale compatibility is pending; Phase 2 is next. Replication and cutover have not started.

Checked items record work completed in the migration chat; unchecked items remain to be done. See [source assessment](01-source-assessment.md) for findings.

## Phase 1 — Discovery & Readiness (complete)

- [x] Confirm versions: Supabase 17.4 (ARM64); OVH 17.11 (x86-64).
- [x] Inventory 26 `public` tables, extensions, keys, constraints, indexes, functions, triggers, views, RLS, and custom types.
- [x] Verify logical replication settings, available slots, publications, primary keys, and replica identities.
- [x] Confirm source Alembic revision matches repository head: `c03_youtube_scan_controls`.
- [x] Capture 18 sequences and ownership, row counts, sizes, source locale, and empty target application-table baseline.

## Phase 2 — Prepare OVH Target (next)

- [ ] Compare target encoding, collation/provider/version, CType, and timezone with the source; resolve compatibility differences.
- [ ] Configure migration tools, restricted credentials, secure direct connectivity, and target permissions; keep secrets outside Git.
- [ ] Build the target schema with Alembic; verify revision and compare tables, columns, types, keys, constraints, indexes, enums, and sequences.
- [ ] Resolve schema drift and confirm the explicit replication table list; handle any Alembic-populated metadata separately from the initial copy.

## Phase 3 — Configure Logical Replication

- [ ] Create a dedicated Supabase publication for the selected application tables.
- [ ] Verify slot/sender capacity, including initial-copy workers; create the OVH subscription using a direct source connection.
- [ ] Confirm the migration slot is active, subscriber workers run, and initial synchronization starts.

## Phase 4 — Initial Data Synchronization

- [ ] Keep FastAPI and background writers on Supabase; allow the initial copy to finish.
- [ ] Monitor table synchronization, apply errors, replication lag, and retained source WAL.
- [ ] Confirm every published table is synchronized; coordinate any schema changes on both databases.

## Phase 5 — Validate Target

- [ ] Compare row counts at a consistent point; validate critical records, foreign-key integrity, UNIQUE/CHECK rules, indexes, and enums.
- [ ] Validate application queries and FastAPI reads against OVH without application writes to the replication target.
- [ ] Save validation evidence; repeat final count/integrity checks during the write pause.

## Phase 6 — Prepare Cutover & Rollback

- [ ] Prepare target connection settings, deployment steps, smoke tests, and sequence-reconciliation commands.
- [ ] Define the write-pause window covering FastAPI, workers, schedulers, and manual writers.
- [ ] Confirm backups/recovery, rollback triggers, and the observation window.
- [ ] Document rollback before target writes and reconciliation of OVH-only changes if rollback is needed afterward.

## Phase 7 — Production Cutover

- [ ] Stop every source writer; record the final source WAL position and wait until OVH has applied through it with all tables synchronized.
- [ ] Run final source/target count and integrity checks; reconcile all 18 target sequences.
- [ ] Disable the forward subscription after catch-up, before enabling application writes on OVH; keep source writers stopped.
- [ ] Switch FastAPI and background jobs to OVH, restart/redeploy, and run read-only smoke tests.
- [ ] Re-enable writes on OVH only after checks pass; verify a controlled write.

## Phase 8 — Post-Cutover Validation

- [ ] Verify inserts/sequence IDs, updates, deletes, critical API endpoints, background jobs, and dashboards/admin UI.
- [ ] Monitor PostgreSQL logs, application errors, and performance; record results.

## Phase 9 — Rollback Window

- [ ] Keep Supabase intact with application writes stopped; monitor retained WAL and manage the migration slot deliberately.
- [ ] If rollback is required after OVH accepts writes, stop writers and reconcile new changes before switching back.
- [ ] Confirm OVH stability through the agreed observation window and sign off.

## Phase 10 — Close Migration

- [ ] Remove the migration subscription/publication and verify no migration or synchronization slots remain.
- [ ] Archive evidence, rotate temporary credentials, and update deployment/architecture documentation.
- [ ] Retire Supabase only after final approval and retention requirements are met.

Schema changes and sequence state require separate handling: see [PostgreSQL 17 replication restrictions](https://www.postgresql.org/docs/17/logical-replication-restrictions.html). Subscription cleanup and retained-slot behavior are documented in [subscription management](https://www.postgresql.org/docs/17/logical-replication-subscription.html).
