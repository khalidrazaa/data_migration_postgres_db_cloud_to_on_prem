# Supabase → OVH PostgreSQL Migration Plan

Migrate ExplainIT from Supabase PostgreSQL to PostgreSQL 17 on OVH using logical replication, a short write pause, validation, and a defined rollback window.

**Current status:** Subscription `explainit_migration_sub` is copying 24 application tables from Supabase to OVH `explainit`, using source slot `explainit_migration_slot`. The user applied the subscription-specific timeout fix, and the agent verified it read-only. The latest saved checks found 23 tables ready and increasing `trend_videos` COPY progress, with no new errors between fresh samples. Monitor the remaining copy using target preparation section 13. FastAPI stays on Supabase; validation and cutover have not been completed.

Checked items record work completed in the migration chat; unchecked items remain to be done. See [source assessment](01-source-assessment.md) for findings and [target preparation](02-target-preparation.md) for the next command.

## Phase 1 — Discovery & Readiness (complete)

- [x] Confirm versions: Supabase 17.4 (ARM64); OVH 17.11 (x86-64).
- [x] Inventory 26 `public` tables, extensions, keys, constraints, indexes, functions, triggers, views, RLS, and custom types.
- [x] Verify logical replication settings, available slots, publications, primary keys, and replica identities.
- [x] Confirm source Alembic revision matches repository head: `c03_youtube_scan_controls`.
- [x] Capture 18 sequences and ownership, row counts, sizes, source locale, and empty target application-table baseline.

## Phase 2 — Prepare OVH Target (in progress)

- [x] Confirm OVH ICU `en-US` support and role capabilities; retain the previously observed source/target collation versions `153.120` / `153.128` as historical validation context.
- [x] Create the canonical `explainit` target with owner `explainit_admin`, UTF8, and ICU `en-US` (user reported completion).
- [x] Verify the new target's connectivity, ICU `en-US`, and zero public tables with `prepare_target_schema.py --check-target` before Alembic (user-reported pass).
- [x] Verify the fresh target's CType, timezone, encoding, and installed collation version separately using the read-only comparison: `en_US.utf8`, `Etc/UTC`, UTF8, ICU `en-US`, version `153.128`.
- [ ] Configure migration tools, restricted credentials, secure direct connectivity, and target permissions; keep secrets outside Git.
- [x] Build the target schema with Alembic through `c03_youtube_scan_controls` (user-reported successful launcher completion).
- [x] Verify actual revisions and compare tables, columns, types, keys, constraints, indexes, enums, and sequences using `compare_schema.py`; review and accept column-order differences for logical replication.
- [x] Confirm 24 application tables for replication, excluding `alembic_version` and source-only `schema_migrations` tracking metadata.
- [x] Remove the two target seed rows from `host_sites` and `popular_scan_settings` before the initial copy; user reported both remaining counts as `0`. Preserve target Alembic metadata.

## Phase 3 — Configure Logical Replication

- [x] Obtain the direct Supabase endpoint: `db.kfyztavryuuwagsfrtrs.supabase.co:5432`, database and username `postgres`.
- [x] Identify the actual OVH PostgreSQL container: `postgres-db`, image `postgres:17` (user-reported running container list).
- [x] Verify direct source connectivity and replication authentication from the OVH host network using a temporary psql client and `IDENTIFY_SYSTEM`.
- [x] Create and test the additional IPv6-only network `explainit-migration-ipv6` using a temporary psql client.
- [x] Attach `postgres-db` to the tested IPv6-only network; verify original IPv4 address/gateway and `127.0.0.1:5432` binding are preserved, with an additional working IPv6 address/gateway.
- [x] Verify direct replication-protocol connectivity from `postgres-db` using `IDENTIFY_SYSTEM` (user-reported source system/database match).
- [x] Verify target subscription permissions and worker/slot capacity: superuser/CREATE/subscription access, logical workers 4, total workers 8, sync workers 2, slots 10, zero existing subscriptions.
- [x] Create source publication `explainit_migration_pub`; user reported 24 tables and the agent verified exact membership and all four published operations read-only.
- [x] Pass the subscription helper's live read-only preflight: exact publication, empty target, matching revisions, no existing objects, and capacity for all three planned replication workers.
- [x] Verify slot/sender capacity, including initial-copy workers; create OVH subscription `explainit_migration_sub` with source slot `explainit_migration_slot` using a direct source connection (user-reported successful apply).
- [x] Confirm the migration slot is active, subscriber apply/two sync workers run, and initial synchronization starts (agent read-only verification).

## Phase 4 — Initial Data Synchronization

- [ ] Keep FastAPI and background writers on Supabase; allow the initial copy to finish.
- [ ] Monitor table synchronization, apply errors, replication lag, and retained source WAL using `monitor_replication.py`; the source timeout override was applied and verified. Latest checks found 23 ready and increasing `trend_videos` COPY progress without new errors; wait for all 24 tables to synchronize.
- [ ] Confirm every published table is synchronized; coordinate any schema changes on both databases.

## Phase 5 — Validate Target

- [ ] Compare row counts at a consistent point; validate critical records, foreign-key integrity, UNIQUE/CHECK rules, indexes, and enums.
- [ ] Validate application queries and FastAPI reads against OVH without application writes to the replication target.
- [ ] Save validation evidence; repeat final count/integrity checks during the write pause.

## Phase 6 — Prepare Cutover & Rollback

- [ ] Prepare target connection settings, deployment steps, smoke tests, and sequence-reconciliation commands.
- [ ] Persist the subscriber's additional IPv6 network in the OVH deployment configuration so container recreation preserves connectivity.
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
