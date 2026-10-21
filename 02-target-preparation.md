# Target Preparation — OVH PostgreSQL

**Status:** User verified `explainit_icu` on 2026-10-22: owner `explainit_admin`, encoding `UTF8`, provider ICU, locale `en-US`, collation version `153.128`. Local migration connection settings now select this database. Applying the application schema is the next action; data transfer remains pending.

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

**Recommendation at this stage:** Match the source ICU provider and `en-US` locale before creating the application schema and loading data. The provider difference alone does not establish that logical replication will fail. Sections 3–4 record the subsequent creation and verification of an ICU target.

## 2. Check ICU availability and database creation privileges — completed

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

Result reported by the user; this query was not rerun by the agent:

| db_user | can_create_database | icu_available |
| --- | --- | --- |
| `explainit_admin` | `true` | `true` |

**Finding:** The current role can create databases, and ICU collations are available on the target.

`can_create_database` checks the current role's superuser/CREATEDB flags. `icu_available = true` confirms ICU collations are present in this database; `false` requires further investigation and does not alone prove the server lacks ICU support. References: [PostgreSQL 17 roles](https://www.postgresql.org/docs/17/view-pg-roles.html) and [collation support](https://www.postgresql.org/docs/17/collation.html).

## 3. Create an ICU target database — completed

Create a separate target named `explainit_icu`, using the source provider `icu` and locale `en-US`. Its owner will be `explainit_admin`. The existing `explainit` database is retained.

Run this command on **OVH** as `explainit_admin`, with **autocommit enabled** and outside any transaction block:

```sql
CREATE DATABASE explainit_icu
    WITH OWNER = explainit_admin
         TEMPLATE = template0
         ENCODING = 'UTF8'
         LOCALE_PROVIDER = icu
         ICU_LOCALE = 'en-US'
         LC_COLLATE = 'en_US.utf8'
         LC_CTYPE = 'en_US.utf8';
```

`template0` allows a different locale provider from the default template. The LC_COLLATE/LC_CTYPE names use the target's observed OS locale spelling. Leave `COLLATION_VERSION` unspecified so PostgreSQL records the installed provider's version. See [PostgreSQL 17 CREATE DATABASE](https://www.postgresql.org/docs/17/sql-createdatabase.html).

Result reported by the user; the command was not executed by the agent:

```text
CREATE DATABASE

Query returned successfully in 284 msec.
```

**Finding:** The creation command succeeded. Verify the new database before changing target connection settings or preparing the schema.

## 4. Verify the new target database — completed

The initial verification attempt used `WHERE datname = current_database()` and returned the following user-reported result:

| database_name | database_owner | encoding | provider_code | provider_locale | collation_version |
| --- | --- | --- | --- | --- | --- |
| `explainit` | `explainit_admin` | `UTF8` | `c` | NULL | `2.41` |

**Finding:** The query connection was still using the original `explainit` database. This result does not verify `explainit_icu`.

Run the following read-only query from the **same OVH connection** to inspect `explainit_icu` by name. The [pg_database catalog](https://www.postgresql.org/docs/17/catalog-pg-database.html) is shared across databases in the same PostgreSQL cluster, so reconnecting is not required for this check.

```sql
SELECT
    datname AS database_name,
    pg_get_userbyid(datdba) AS database_owner,
    pg_encoding_to_char(encoding) AS encoding,
    datlocprovider AS provider_code,
    datlocale AS provider_locale,
    datcollversion AS collation_version
FROM pg_database
WHERE datname = 'explainit_icu';
```

Expected: database `explainit_icu`, owner `explainit_admin`, encoding `UTF8`, provider code `i` (ICU), and provider locale `en-US`. Record the installed collation version; it need not equal the source's `153.120`. Source and target ICU versions may still differ in behavior.

Result for `explainit_icu` reported by the user:

| database_name | database_owner | encoding | provider_code | provider_locale | collation_version |
| --- | --- | --- | --- | --- | --- |
| `explainit_icu` | `explainit_admin` | `UTF8` | `i` | `en-US` | `153.128` |

**Finding:** Ownership, encoding, provider, and provider locale match the intended target. The target ICU version `153.128` differs from the recorded source version `153.120`; include text sorting/case behavior in later application validation. This catalog check does not itself prove a connection to the new database; the schema launcher checks the actual connection before applying migrations.

## 5. Configure the local migration connection — completed

The agent updated this workspace's ignored `.env`:

- `TARGET_DATABASE_URL`: database changed to `explainit_icu`, retaining the target endpoint, credentials, and async driver.
- `ALEMBIC_DATABASE_URL`: now uses the same target with the synchronous `postgresql+psycopg2` driver. It previously selected the source.

Source connection settings and other values were preserved. No credentials are recorded here. Only the local migration workspace's `.env` was edited; backend application configuration was not changed.

## 6. Apply the application schema — pending

From this migration workspace in PowerShell, run:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\prepare_target_schema.py' --backend-dir "$fastApiRepo"
```

Set `$fastApiRepo` to the separate FastAPI repository's location if it differs from the path above. The repository is used in place; it is not copied into or edited by this migration workspace.

The [schema launcher](prepare_target_schema.py) reads this migration workspace's `.env`, verifies the actual target is `explainit_icu` with ICU `en-US`, and requires zero public application tables. It then runs Alembic from the external repository selected by `--backend-dir` through the assessed revision `c03_youtube_scan_controls`, passing the target URL explicitly in the child process. That repository's existing `.venv` provides the required dependencies.

The launcher uses a synchronous driver and escapes URL percent signs for this backend's Alembic ConfigParser handling. References: [SQLAlchemy psycopg2 dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg2) and [Alembic configuration](https://alembic.sqlalchemy.org/en/latest/tutorial.html).

**Result:** Pending. The agent has not connected to a database or executed Alembic migrations. Return the command output before proceeding to schema comparison.

After schema creation, compare actual table names and definitions with the source; matching Alembic revisions alone does not establish schema equality. Backend migrations also seed `popular_scan_settings` and `host_sites`, in addition to Alembic's `alembic_version` row. Account for these populated target tables before the initial replication copy.

Publication/subscription setup and data transfer remain pending. Continue one step at a time after reviewing the schema command output.
