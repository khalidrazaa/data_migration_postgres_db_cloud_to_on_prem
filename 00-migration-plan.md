# Supabase to OVH PostgreSQL Migration - Complete

**Status: COMPLETE - SUCCESSFUL.**

Completed and accepted at the user's request on **25 October 2026 (Asia/Calcutta)**. The database migration is complete for the **19 retained application tables**. OVH `explainit` is live, Supabase application writers are stopped as confirmed by the user, and incoming replication remains disabled.

This is the final completion record. Historical preparation and executed commands remain in [02-target-preparation.md](02-target-preparation.md); source findings remain in [01-source-assessment.md](01-source-assessment.md). Machine-readable completion status: [migration_completion.json](migration_completion.json).

## Final scope

| Item | Final result |
| --- | --- |
| Source | Supabase database `postgres` |
| Live target | OVH Docker PostgreSQL, database `explainit`, owner `explainit_admin` |
| Schema revision | `c03_youtube_scan_controls` |
| Initial synchronization | All original 24 application tables finished copying |
| Final validated scope | 19 application tables, with 13 owned ID sequences |
| Application switch | Approximately 24 October 2026, reported by the user |
| Incoming replication | Disabled; zero live workers verified |

Five trend/YouTube tables were excluded from the final validated scope: `discovered_trends`, `trend_ideas`, `trend_items`, `trend_videos`, and `volume_points`. Their existing source and target tables/data remain intact. Shared niche, keyword and settings tables remain in scope. Exact table lists are recorded in [replication_scope.json](replication_scope.json).

## Verified results

The latest read-only validation evidence was collected on **25 October 2026, approximately 11:55-11:58 IST**.

| Check | Result |
| --- | --- |
| Source / OVH records in retained scope | 431 / 433 |
| Source IDs missing on OVH | **0** |
| Identical shared rows | 419 |
| Changed shared rows | 12, with field differences recorded below |
| Additional OVH rows | Two leads |
| Canonically identical tables | 17 of 19 |
| Owned ID sequences | **All 13 pass**; eight behind counters were corrected |
| Foreign-key integrity | **Nine checks, zero orphan rows** |
| Index health | **All 51 indexes valid, ready and live** |
| Replication state | **Disabled, zero workers** |

Six existing leads differ only in `status`, `internal_notes` and `updated_at`; six niche rows differ only in `last_scanned_at`. These live OVH values and the two additional leads were preserved. Current comparisons use independent snapshots of the stopped-source/live-target databases; they do not reconstruct every change during the earlier cutover overlap.

The sequence repair committed successfully. Its original post-commit warning was caused by a verifier formatting bug, now fixed. Read-only verification confirmed stable metadata across transaction boundaries and zero remaining sequence corrections. No repeat repair is needed.

## Completed migration work

- [x] Assess source schema, versions, keys, locale and replication readiness.
- [x] Prepare canonical OVH `explainit`, apply Alembic and compare compatible application schemas.
- [x] Establish SSH administration and direct IPv6 replication connectivity.
- [x] Create the publication/subscription and finish the original 24 initial copies.
- [x] Apply the agreed five-table feature exclusion, leaving 19 retained tables.
- [x] Record the user's switch to live OVH and stopped Supabase writers.
- [x] Disable incoming replication and verify zero workers.
- [x] Correct eight ID counters and verify all 13 retained-owned sequences.
- [x] Compare retained rows and record live OVH field differences without overwriting them.
- [x] Verify actual foreign-key integrity and index health.
- [x] Record the user's successful migration sign-off and update completion records.

## Validation evidence

- [Live data and sequence audit](live_target_audit.json)
- [Foreign-key and index audit](live_target_integrity.json)
- [Field-level change counts](live_target_field_changes.json)
- [Sequence repair verification](target_sequence_repair_verification.json)
- [Latest read-only sequence plan](target_sequence_repair_plan.json)
- [Original schema comparison](schema_comparison.json)

## Post-migration follow-ups

These are tracked separately from the accepted database migration. They are not recorded as performed by the database audits.

| Follow-up | Recorded state |
| --- | --- |
| Formal application read/create/update smoke tests | No result recorded in this workflow |
| Recoverable OVH backup and deployment persistence | Confirmation remains separate |
| Migration subscription/publication/slot cleanup | Objects preserved; deliberate cleanup pending |
| Temporary credential rotation and final deployment notes | Follow-up pending |
| Supabase retirement | Separate decision; preserve source data until approved |

Keep incoming Supabase replication disabled on live OVH. Migration monitors may report `replication_running = false`; that is intentional after this cutover and is not a reason to re-enable it.
