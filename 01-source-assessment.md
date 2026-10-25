# Source Assessment — Supabase → OVH PostgreSQL

**Outcome: MIGRATION COMPLETE - SUCCESSFUL, accepted by the user on 25 October 2026 (Asia/Calcutta).** Source assessment is complete, and the retained scope is migrated to live OVH `explainit`. All original 24 application tables finished initial copying; the agreed trend/YouTube exclusion leaves 19 validated tables. Final read-only checks accounted for all 431 source IDs, with two additional OVH leads, recorded workflow/scan-timestamp changes, all 13 owned sequences passing, zero orphans across nine foreign keys, and 51 healthy indexes. Supabase writers are stopped as confirmed by the user; incoming replication is disabled with zero workers. See the [completion record](00-migration-plan.md), [executed preparation and validation](02-target-preparation.md), and [machine-readable status](migration_completion.json). Operational follow-ups are recorded separately.

Scope: application objects in `public`. Results are historical findings from the migration chat and existing assessment notes. SQL is consolidated into read-only equivalents for repeatability; it was not rerun for this document. Long inventories are summarized below.

## 1. Source and target versions

Run on **both databases** to establish version and platform compatibility.

```sql
SELECT version();
```

| Database | Observed version | Platform |
| --- | --- | --- |
| Supabase source | PostgreSQL 17.4 | ARM64 |
| OVH target | PostgreSQL 17.11 | x86-64 |

**Conclusion:** Both run PostgreSQL 17. Logical replication is the planned migration method; target schema and locale checks still apply.

## 2. Schema inventory and migration scope

Run on **source** to separate application objects from Supabase-managed schemas.

```sql
SELECT nspname AS schema_name
FROM pg_namespace
WHERE nspname NOT LIKE 'pg_%'
  AND nspname <> 'information_schema'
ORDER BY nspname;
```

**Observed:** `auth`, `extensions`, `graphql`, `graphql_public`, `public`, `realtime`, `storage`, `vault`.

**Conclusion:** Migrate the application schema, primarily `public`. Supabase-managed schemas need separate dependency decisions.

## 3. Application table inventory

```sql
-- Source: list public tables and include the inventory count.
SELECT c.relname AS table_name, count(*) OVER () AS table_count
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname;
```

**Observed:** **26 tables** in `public`; managed tables identified separately.

**Conclusion:** Use the table inventory to define an explicit migration publication. Account for Alembic metadata separately when preparing the target.

## 4. Extensions and column defaults

```sql
-- Source: installed extensions and application default expressions.
SELECT e.extname, e.extversion, n.nspname AS extension_schema
FROM pg_extension e
JOIN pg_namespace n ON n.oid = e.extnamespace
ORDER BY e.extname;

SELECT table_name, column_name, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND column_default IS NOT NULL
ORDER BY table_name, ordinal_position;
```

**Observed:** `pg_stat_statements`, `pgcrypto`, `plpgsql`, `supabase_vault`, `uuid-ossp`. Defaults and public functions were inspected; no application dependency requiring a Supabase-specific extension on OVH was identified.

**Conclusion:** Install target extensions according to actual application dependencies. Verify defaults during schema comparison.

## 5. Logical replication readiness, slots and publications

```sql
-- Source: configured capacity and existing replication objects.
SELECT current_setting('wal_level') AS wal_level,
       current_setting('max_replication_slots') AS max_replication_slots,
       current_setting('max_wal_senders') AS max_wal_senders;

SELECT slot_name, plugin, slot_type, database, active
FROM pg_replication_slots
ORDER BY slot_name;

SELECT pubname, puballtables, pubinsert, pubupdate, pubdelete, pubtruncate
FROM pg_publication
ORDER BY pubname;

SELECT pubname, schemaname, tablename
FROM pg_publication_tables
ORDER BY pubname, schemaname, tablename;
```

| Check | Observed result |
| --- | --- |
| WAL level | `logical` |
| Maximum replication slots | 5 |
| Maximum WAL senders | 5 |
| Existing slots | 0 |
| Existing publication | `supabase_realtime` |
| Tables in `supabase_realtime` | 0 |

**Conclusion:** Source settings support logical replication. Create dedicated migration objects during setup; recheck capacity, including initial-copy workers, and validate replication access.

## 6. Primary keys and replica identity

```sql
-- Source: include tables with no PK so omissions remain visible.
SELECT c.relname AS table_name, c.relreplident AS replica_identity,
       pk.conname AS primary_key,
       pg_get_constraintdef(pk.oid) AS key_definition
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_constraint pk ON pk.conrelid = c.oid AND pk.contype = 'p'
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname;
```

**Observed:** All **26 tables** have primary keys and use replica identity `d` (`DEFAULT`).

**Conclusion:** Primary keys identify rows for replicated updates and deletes. Preserve compatible keys on the target. [PostgreSQL replica identity requirements](https://www.postgresql.org/docs/17/logical-replication-publication.html).

## 7. Alembic baseline

```sql
-- Source: current migration revision.
SELECT version_num FROM public.alembic_version;
```

From the application repository:

```sh
alembic heads
```

| Check | Observed revision |
| --- | --- |
| Source database | `c03_youtube_scan_controls` |
| Repository head | `c03_youtube_scan_controls` |

**Conclusion:** Revision identifiers match. Build and compare the target schema; matching revision names alone do not prove absence of schema drift.

## 8. Target cleanliness

```sql
-- Target: application table baseline before schema preparation.
SELECT count(*) AS public_table_count
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p');
```

**Observed:** OVH target database `explainit`; **0 application tables** in `public`.

**Conclusion:** Target application-table baseline is clean. After applying Alembic, handle any populated metadata table before the initial replication copy.

## 9. Sequence inventory and ownership

```sql
-- Source: sequence settings/state; this does not advance sequences.
SELECT sequencename, data_type, start_value, increment_by,
       min_value, max_value, cycle, cache_size, last_value
FROM pg_sequences
WHERE schemaname = 'public'
ORDER BY sequencename;

-- LEFT JOIN retains unowned sequences; a = serial, i = identity ownership.
SELECT s.relname AS sequence_name,
       tn.nspname AS table_schema, t.relname AS table_name,
       a.attname AS column_name
FROM pg_class s
JOIN pg_namespace sn ON sn.oid = s.relnamespace
LEFT JOIN pg_depend d
  ON d.classid = 'pg_class'::regclass AND d.objid = s.oid
 AND d.objsubid = 0 AND d.refclassid = 'pg_class'::regclass
 AND d.refobjsubid > 0 AND d.deptype IN ('a', 'i')
LEFT JOIN pg_class t ON t.oid = d.refobjid
LEFT JOIN pg_namespace tn ON tn.oid = t.relnamespace
LEFT JOIN pg_attribute a
  ON a.attrelid = t.oid AND a.attnum = d.refobjsubid AND NOT a.attisdropped
WHERE sn.nspname = 'public' AND s.relkind = 'S'
ORDER BY s.relname;
```

**Observed:** **18 sequences**, all mapped to owning table columns. Examples: `articles_id_seq`, `trend_videos_id_seq`, `trend_ideas_id_seq`, `discovered_trends_id_seq`.

**Conclusion:** Reconcile all 18 target sequences after stopping source writes and catching up replication, before enabling target writes. [Logical replication does not synchronize sequence state](https://www.postgresql.org/docs/17/logical-replication-restrictions.html).

## 10. Application triggers

```sql
-- Source: exclude internal constraint triggers.
SELECT c.relname AS table_name, t.tgname,
       pg_get_triggerdef(t.oid) AS definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal
ORDER BY c.relname, t.tgname;
```

**Observed:** **0 non-internal application triggers**.

**Conclusion:** No custom table-trigger behavior was identified to recreate.

## 11. Views, materialized views and functions

```sql
-- Source: derived relations and their definitions.
SELECT c.relname AS object_name, c.relkind,
       pg_get_viewdef(c.oid, true) AS definition
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm')
ORDER BY c.relname;

-- Application routines, excluding extension-owned functions.
SELECT p.proname,
       pg_get_function_identity_arguments(p.oid) AS arguments,
       pg_get_functiondef(p.oid) AS definition
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
  AND NOT EXISTS (
    SELECT 1 FROM pg_depend d
    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid
      AND d.refclassid = 'pg_extension'::regclass AND d.deptype = 'e'
  )
ORDER BY p.proname, arguments;
```

**Observed:** **0 views**, **0 materialized views**; no custom application functions reported.

**Conclusion:** No additional application view or function definitions were identified for migration.

## 12. Foreign keys

```sql
-- Source: definitions capture columns, referenced tables and delete actions.
SELECT c.conrelid::regclass AS table_name, c.conname,
       c.confrelid::regclass AS referenced_table,
       rn.nspname <> n.nspname AS cross_schema,
       pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_class r ON r.oid = c.confrelid
JOIN pg_namespace rn ON rn.oid = r.relnamespace
WHERE n.nspname = 'public' AND c.contype = 'f'
ORDER BY table_name, c.conname;
```

**Observed:** **12 FKs**, **0 cross-schema FKs**. Delete actions include `CASCADE`, `SET NULL` and `NO ACTION`.

**Conclusion:** Recorded FK dependencies stay within `public`. Preserve definitions and validate target referential integrity after loading.

## 13. UNIQUE and CHECK constraints

```sql
-- Source: capture business integrity rules, not just their names.
SELECT c.conrelid::regclass AS table_name, c.conname, c.contype,
       c.convalidated, pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname = 'public' AND c.contype IN ('u', 'c')
ORDER BY table_name, c.contype, c.conname;
```

**Observed:** **9 UNIQUE constraints**, **3 CHECK constraints**.

**Conclusion:** Compare definitions and validation state on the target. Standalone unique indexes also need the next check.

## 14. Indexes, including partial unique indexes

```sql
-- Source: capture expressions and predicates as well as uniqueness.
SELECT t.relname AS table_name, x.relname AS index_name,
       i.indisprimary, i.indisunique, i.indisvalid,
       pg_get_indexdef(i.indexrelid) AS definition,
       pg_get_expr(i.indpred, i.indrelid) AS predicate
FROM pg_index i
JOIN pg_class t ON t.oid = i.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN pg_class x ON x.oid = i.indexrelid
WHERE n.nspname = 'public'
ORDER BY t.relname, x.relname;
```

**Observed:** Primary-key, unique, composite and partial indexes inventoried. Critical example: `uq_content_active_topic`, enforcing one queued/running content workflow per topic.

**Conclusion:** Compare full index definitions, including key order, expressions and partial predicates. A constraint count does not capture every uniqueness rule.

## 15. Row-level security and policies

```sql
-- Source: enabled/forced RLS flags and policy definitions.
SELECT c.relname AS table_name, c.relrowsecurity, c.relforcerowsecurity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY c.relname;

SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;
```

**Observed:** **0/26** tables with RLS enabled; **0** forced-RLS tables; **0 policies**.

**Conclusion:** No application-table RLS dependency was identified. Target roles and grants still require preparation.

## 16. Custom type: `public.trendstatus`

```sql
-- Source: enum/domain/standalone composite inventory, excluding table row types.
SELECT t.typname, t.typtype
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
LEFT JOIN pg_class c ON c.oid = t.typrelid
WHERE n.nspname = 'public'
  AND (t.typtype IN ('e', 'd') OR (t.typtype = 'c' AND c.relkind = 'c'))
ORDER BY t.typname;

-- Preserve label order; inspect direct application column usage separately.
SELECT t.typname, e.enumsortorder, e.enumlabel
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
JOIN pg_enum e ON e.enumtypid = t.oid
WHERE n.nspname = 'public' AND t.typname = 'trendstatus'
ORDER BY e.enumsortorder;

SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND udt_schema = 'public' AND udt_name = 'trendstatus'
ORDER BY table_name, ordinal_position;
```

**Observed:** `public.trendstatus` labels in order: **`Open`, `Processed`, `Ignored`**. Used by `public.trend_items.status`; matches Alembic, the SQLAlchemy model and schema snapshot.

**Conclusion:** Preserve enum labels, case and order when creating the target schema.

## 17. Source size and exact row counts

```sql
-- Source: total relation size includes table, indexes and TOAST.
SELECT c.relname AS table_name, c.reltuples::bigint AS estimated_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
       pg_size_pretty(sum(pg_total_relation_size(c.oid)) OVER ()) AS public_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
ORDER BY pg_total_relation_size(c.oid) DESC, c.relname;

-- Exact counts from one snapshot; ONLY avoids inheritance double-counting.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

WITH counts AS MATERIALIZED (
  SELECT tablename AS table_name,
         (xpath('/table/row/row_count/text()', query_to_xml(
           format('SELECT count(*) AS row_count FROM ONLY %I.%I',
                  schemaname, tablename), false, false, ''
         )))[1]::text::bigint AS exact_rows
  FROM pg_tables
  WHERE schemaname = 'public'
)
SELECT table_name, exact_rows, sum(exact_rows) OVER () AS total_rows
FROM counts
ORDER BY exact_rows DESC, table_name;

COMMIT;
```

| Recorded baseline | Observed value |
| --- | ---: |
| Approximate `public` table footprint, including indexes/TOAST | 264 MB |
| `trend_videos` footprint | Approximately 258 MB |
| `trend_videos` exact rows | 42,459 |
| `trend_ideas` exact rows | 15,693 |
| `discovered_trends` exact rows | 5,267 |
| **Total exact rows across 26 tables** | **63,816** |

**Conclusion:** The initial copy is modest; `trend_videos` dominates storage. These are historical counts, with the largest tables shown here. Refresh the full per-table counts at a common source/target point during final validation.

## 18. Source locale baseline

```sql
-- Source: PostgreSQL 17 provider locale is pg_database.datlocale.
SELECT d.datname AS database_name,
       pg_encoding_to_char(d.encoding) AS encoding,
       d.datcollate AS lc_collate, d.datctype AS lc_ctype,
       CASE d.datlocprovider
         WHEN 'i' THEN 'icu' WHEN 'c' THEN 'libc' WHEN 'b' THEN 'builtin'
       END AS locale_provider,
       d.datlocale AS provider_locale, d.datcollversion AS collation_version,
       current_setting('TimeZone') AS session_timezone
FROM pg_database d
WHERE d.datname = current_database();
```

| Setting | Observed source value |
| --- | --- |
| Database | `postgres` |
| Encoding | `UTF8` |
| LC_COLLATE / LC_CTYPE | `en_US.UTF-8` / `en_US.UTF-8` |
| Locale provider / provider locale | `icu` / `en-US` |
| Collation version | `153.120` |
| Assessment session timezone | `UTC` |

**Conclusion:** Run the same check on OVH and resolve locale differences before replication setup. The recorded timezone is session-effective; collation/provider differences require review of sorting and uniqueness behavior. [PostgreSQL 17 locale catalog fields](https://www.postgresql.org/docs/17/catalog-pg-database.html).

## Assessment completion and final status

The source assessment and accepted 19-table database migration are **complete and successful**. Initial copying, the application switch, retained-data validation, sequence correction, and disabling incoming replication have been recorded. The findings above remain historical assessment results; schema evidence is preserved in [schema_comparison.json](schema_comparison.json). Refer to the [final completion record](00-migration-plan.md) and [migration sign-off](02-target-preparation.md#20-final-migration-sign-off---complete-and-successful). Remaining operational follow-ups are separate from this accepted completion. Keep incoming replication disabled on live OVH.

