# Target Preparation — OVH PostgreSQL

**Status:** Target locale result received on 2026-10-22. Collation provider compatibility remains unresolved. The next action is the read-only ICU and privilege check below.

Scope: prepare the target for the logical replication migration described in [the migration plan](00-migration-plan.md). Source baseline: [source assessment, section 18](01-source-assessment.md#18-source-locale-baseline).

## 1. Target locale check — completed

Run on the **OVH target**:

```sql
SELECT
    current_database() AS database_name,
    pg_encoding_to_char(encoding) AS encoding,
    datcollate AS lc_collate,
    datctype AS lc_ctype,
    CASE datlocprovider
        WHEN 'i' THEN 'icu'
        WHEN 'c' THEN 'libc'
        WHEN 'b' THEN 'builtin'
    END AS locale_provider,
    datlocale AS provider_locale,
    datcollversion AS collation_version,
    current_setting('TimeZone') AS session_timezone
FROM pg_database
WHERE datname = current_database();
```

Result reported by the user; this query was not rerun by the agent:

| Setting | Recorded source | Reported OVH target |
| --- | --- | --- |
| Database | `postgres` | `explainit` |
| Encoding | `UTF8` | `UTF8` |
| LC_COLLATE | `en_US.UTF-8` | `en_US.utf8` |
| LC_CTYPE | `en_US.UTF-8` | `en_US.utf8` |
| Locale provider | `icu` | `libc` |
| Provider locale | `en-US` | NULL (blank in result) |
| Collation version | `153.120` | `2.41` |
| Session timezone | `UTC` | `Etc/UTC` |

**Finding:** Encoding and session timezone are compatible. A NULL provider locale is expected for libc. The difference requiring review is source ICU versus target libc: text sorting and case conversion can differ. The provider-specific version numbers are not directly comparable. See [PostgreSQL 17 locale behavior](https://www.postgresql.org/docs/17/locale.html) and [database locale fields](https://www.postgresql.org/docs/17/catalog-pg-database.html).

**Recommendation:** Match the source ICU provider and `en-US` locale before creating the application schema and loading data. The provider difference alone does not establish that logical replication will fail; compatibility has not yet been resolved. No database recreation or provider change has been performed in this step.

## 2. Check ICU availability and database creation privileges — pending

Run this read-only query on the **OVH target**, using the role intended for target preparation:

```sql
SELECT
    current_user AS db_user,
    (rolsuper OR rolcreatedb) AS can_create_database,
    EXISTS (
        SELECT 1
        FROM pg_collation
        WHERE collprovider = 'i'
    ) AS icu_available
FROM pg_roles
WHERE rolname = current_user;
```

**Result:** Pending. Return the result row before choosing the next migration command.

`can_create_database` checks the current role's superuser/CREATEDB flags. `icu_available = true` confirms ICU collations are present in this database; `false` requires further investigation and does not alone prove the server lacks ICU support. References: [PostgreSQL 17 roles](https://www.postgresql.org/docs/17/view-pg-roles.html) and [collation support](https://www.postgresql.org/docs/17/collation.html).

Target schema preparation, publication/subscription setup, and data transfer remain pending. Continue one step at a time after reviewing this result.
