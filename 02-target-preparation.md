# Target Preparation — OVH PostgreSQL

**Target database: `explainit`.**

**Status (2026-10-25 IST):** OVH is live, with the switch reported as roughly 24 October; Supabase writers are stopped. Incoming replication is disabled with zero live workers. Read-only audits found all 431 source records present in the 19-table retained scope, two additional OVH leads, no orphans across nine foreign keys, and 51 healthy indexes. Six existing leads differ only in workflow fields. Eight of the 13 ID sequences are behind existing IDs; section 18 contains the prepared correction. No sequence values have been changed yet. Keep incoming replication disabled.

Source findings: [01-source-assessment.md](01-source-assessment.md). Full sequence: [00-migration-plan.md](00-migration-plan.md).

## 1. Confirmed preparation

- The intended owner, `explainit_admin`, has database creation privileges.
- ICU support was confirmed on OVH. The source uses `UTF8`, ICU provider, and locale `en-US`.
- Before the reset, an OVH ICU test target reported collation version `153.128`; the recorded source version is `153.120`. This is historical evidence, not verification of the fresh database. Include text sorting and case conversion in later application validation.
- This workspace's ignored `.env` selects `explainit` for `TARGET_DATABASE_URL`, `ALEMBIC_DATABASE_URL`, and `DB_NAME`. Alembic uses the synchronous `postgresql+psycopg2` driver.
- The local target and Alembic URLs use the working SSH forward at `127.0.0.1:15432`. Credentials and connection options were preserved.
- The source URL and the separate FastAPI repository's configuration were preserved.

Database creation and the successful read-only preflight are user-reported. The latest preflight confirms target connectivity, ICU `en-US`, and zero public tables.

## 2. Create `explainit` — completed

In pgAdmin, open Query Tool on the **OVH server's `postgres` database** as `explainit_admin`. Keep the server connection's **Maintenance database** set to `postgres`.

Enable **autocommit**. Run this command by itself, outside any transaction block:

```sql
CREATE DATABASE explainit
    WITH OWNER = explainit_admin
         TEMPLATE = template0
         ENCODING = 'UTF8'
         LOCALE_PROVIDER = icu
         ICU_LOCALE = 'en-US'
         LC_COLLATE = 'en_US.utf8'
         LC_CTYPE = 'en_US.utf8';
```

`template0` permits selecting the requested provider and locale. Leave `COLLATION_VERSION` unspecified so PostgreSQL records the installed provider's version. References: [PostgreSQL 17 CREATE DATABASE](https://www.postgresql.org/docs/17/sql-createdatabase.html) and [pgAdmin maintenance database](https://www.pgadmin.org/docs/pgadmin4/latest/server_dialog.html).

**Result:** The user replied `done` to this creation command. The subsequent successful read-only preflight confirmed the database name, ICU provider and locale, and zero public tables. No rename step is required.

## 3. Create the application schema — completed

The following command completed on the **local PC**, from this migration folder in PowerShell, with the MobaXterm tunnel running:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\prepare_target_schema.py' --backend-dir "$fastApiRepo"
```

Set `$fastApiRepo` to the actual external FastAPI repository path. The [schema launcher](prepare_target_schema.py) reads `.env` from this migration workspace and runs Alembic from that separate checkout using its Python environment. The local PC must be able to reach OVH PostgreSQL.

Before applying migrations, the launcher verifies the actual connection is to `explainit` with ICU `en-US` and that there are zero public application tables. It explicitly passes the target URL to Alembic and applies the assessed revision `c03_youtube_scan_controls`. Percent signs are escaped for this backend's ConfigParser handling. See [SQLAlchemy psycopg2](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg2) and [Alembic configuration](https://alembic.sqlalchemy.org/en/latest/tutorial.html).

**Result:** Successful, as reported by the user:

```text
Target schema preparation finished. Schema comparison is next.
```

The launcher applies revision `c03_youtube_scan_controls`. Verify the actual revision and schema against the source in the comparison below. Do not rerun schema preparation: its empty-target guard is intended for the initial build only.

### SSH tunnel and read-only preflight — completed

The user confirmed **SSH Tunnel** is enabled in pgAdmin. The local script needs its own forwarded connection; it does not automatically use pgAdmin's tunnel. Its target URL is prepared for local host `127.0.0.1`, port `15432`, database `explainit`.

The user supplied these settings from the working pgAdmin connection:

| Setting | Value |
| --- | --- |
| SSH host | `51.195.109.43` |
| SSH port | `22` |
| SSH username | `ubuntu` |
| Database destination from the SSH server | `127.0.0.1:5432` |
| Local forward for the migration script | `127.0.0.1:15432` |

The working tunnel was opened separately in MobaXterm using **Tools → MobaSSHTunnel → New SSH tunnel → Local port forwarding**, with the settings above, then started. A normal SSH terminal login alone did not create the forward. See [MobaXterm SSH tunnels](https://blog.mobatek.net/post/ssh-tunnels-and-port-forwarding/).

For reference, the equivalent alternative in a PowerShell window on the **local PC** is:

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:15432:127.0.0.1:5432 -p 22 ubuntu@51.195.109.43
```

Authenticate using the user's existing SSH setup. After authentication the terminal may remain blank: `-N` requests forwarding without a remote shell. Keep this window open. The forward is bound only to the local loopback address. `ExitOnForwardFailure` checks initial forwarding setup; the database destination still needs the read-only test below. References: [pgAdmin SSH Tunnel](https://www.pgadmin.org/docs/pgadmin4/latest/server_dialog.html) and [OpenSSH local forwarding](https://man.openbsd.org/ssh.1#L).

**Tunnel status:** Working, confirmed by the user's successful read-only PostgreSQL connection through `127.0.0.1:15432`. Earlier hostname resolution and connection-refused errors are resolved.

After the SSH tunnel is open, use a **second PowerShell window** in this migration folder and the external backend's Python environment:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\prepare_target_schema.py' --check-target
```

`--check-target` checked connectivity, ICU `en-US`, and an empty public schema before migrations. This is historical preflight evidence; after successful schema creation, the target is expected to contain tables and this empty-target check must not be used as a post-build check.

**Latest result:** Passed, as reported by the user:

```text
Read-only target checks passed: explainit, ICU en-US, empty public schema.
```

**Immediate next step:** Prepare the direct source connection in section 6; schema comparison and seed cleanup are complete.

## 4. Compare source and target — completed

Compare actual table names and definitions with the source before creating replication objects. Matching Alembic revisions alone does not prove schema equality.

Keep the MobaXterm tunnel running. From the same local PowerShell folder:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\compare_schema.py'
```

The comparison reads both URLs from this repository's `.env`, uses read-only transactions, and saves schema details and differences to `schema_comparison.json`. It compares public tables, columns, constraints, indexes, enum labels, sequence definitions and ownership, and Alembic revisions. It also identifies target tables that contain rows. Sequence state and source/target row equality are not checked at this stage. PostgreSQL references: [column catalog](https://www.postgresql.org/docs/17/catalog-pg-attribute.html), [sequence definitions](https://www.postgresql.org/docs/17/catalog-pg-sequence.html), and [read-only transactions](https://www.postgresql.org/docs/17/sql-set-transaction.html).

**Result:** The agent ran the comparison against both live databases using read-only transactions. Evidence: [schema_comparison.json](schema_comparison.json).

| Check | Result |
| --- | --- |
| Alembic revision on both databases | `c03_youtube_scan_controls` |
| Source / target public tables | 26 / 25 |
| Application tables | 24 on both; matching definitions except column order below |
| Source-only table | `schema_migrations`, SQL migration-history metadata |
| Column order | `trend_videos.region_code` and `category_title` are swapped; other column properties match |
| Application constraints, indexes, enums, sequence definitions and ownership | Match |
| Populated target tables before seed cleanup | `alembic_version`, `host_sites`, `popular_scan_settings` |
| Fresh target locale | UTF8, ICU `en-US`, collation version `153.128`, LC_COLLATE / LC_CTYPE `en_US.utf8`, session timezone `Etc/UTC` |

The full schemas are not identical: the raw comparison contains seven object differences from the metadata table and swapped column positions. These do not require target DDL changes for the selected application replication scope. PostgreSQL matches replicated columns by name and permits different column order. See [PostgreSQL subscription schema matching](https://www.postgresql.org/docs/17/logical-replication-subscription.html).

Exclude both `alembic_version` and `schema_migrations` from the application publication. The backend's `migrations/migration_script.py` uses `schema_migrations` only to track SQL filenames and application times; the FastAPI runtime does not use it. Preserve source history. If the separate SQL migration runner is ever used on OVH, reconcile its history before running it.

Backend migrations populate `popular_scan_settings` and `host_sites`, in addition to Alembic's `alembic_version` row. Account for these populated target tables before the initial replication copy.

## 5. Clear target seed rows — completed

The agent verified read-only that target `host_sites` contains one row for `explainit.tech`, `popular_scan_settings` contains one row with key `default`, and `public_api_keys` is empty. Every other application table is empty. These are freshly inserted Alembic defaults, not copied source data.

In pgAdmin, open Query Tool on **OVH → explainit** and run:

```sql
DO $cleanup$
BEGIN
    IF current_database() <> 'explainit' THEN
        RAISE EXCEPTION 'Connect to OVH explainit first';
    END IF;
    IF EXISTS (SELECT 1 FROM public.public_api_keys) THEN
        RAISE EXCEPTION 'Target has API key rows; stop and review';
    END IF;
    DELETE FROM public.host_sites WHERE host = 'explainit.tech';
    DELETE FROM public.popular_scan_settings WHERE key = 'default';
END;
$cleanup$;

SELECT 'host_sites' AS table_name, count(*) AS remaining_rows
FROM public.host_sites
UNION ALL
SELECT 'popular_scan_settings', count(*) FROM public.popular_scan_settings;
```

Expected: both counts are `0`. Keep the target Alembic revision row. The API-key guard prevents deleting host rows that would cascade into existing target keys. The user ran this cleanup; the agent did not execute deletion commands.

**Result:** Completed. Earlier attempts were safely stopped because Query Tool was connected to `postgres`. After switching its connection to `explainit`, the user reported:

```text
host_sites               0
popular_scan_settings    0
```

If needed later, change an existing Query Tool connection through its connection link beside connection status: **<New Connection>**, choose server/database/user, **Save**, then select the saved connection. See [pgAdmin Change connection](https://www.pgadmin.org/docs/pgadmin4/latest/query_tool.html#change-connection).

## 6. Prepare the direct source replication connection — next step

The current `SOURCE_DATABASE_URL` uses Supabase's session pooler. It works for the SQL assessment and comparison, but Supabase requires a **direct connection** for logical replication; Supavisor pooler connections do not support it. See [Supabase manual replication FAQ](https://supabase.com/docs/guides/database/replication/manual-replication-faq).

**Direct endpoint obtained:** The user supplied these values from the Supabase project's **Connect → Direct connection** panel:

| Setting | Value |
| --- | --- |
| Host | `db.kfyztavryuuwagsfrtrs.supabase.co` |
| Port | `5432` |
| Database | `postgres` |
| Username | `postgres` |

Keep the password private and outside this document. Leave the existing working source settings unchanged at this step.

**Container discovery — completed:** In the MobaXterm SSH terminal connected to OVH, the user ran:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Image}}'
```

The output identified **`postgres-db`**, image **`postgres:17`**, as the running PostgreSQL container. See [Docker container listing](https://docs.docker.com/reference/cli/docker/container/ls/).

**Initial connection test — attempted:** In that same MobaXterm SSH terminal on OVH, the user ran:

```bash
sudo docker exec -it postgres-db psql -X -W -P pager=off \
  -d 'host=db.kfyztavryuuwagsfrtrs.supabase.co port=5432 dbname=postgres user=postgres sslmode=require connect_timeout=10 replication=database' \
  -c 'IDENTIFY_SYSTEM;'
```

At psql's password prompt, enter the **Supabase database password**, not the OVH database password. Password input is hidden. Share only the output or error. This reads the source server identity without creating replication objects or copying data. On success it returns `systemid`, `timeline`, `xlogpos`, and `dbname`; verify `dbname` is `postgres`. See [PostgreSQL replication connection test](https://www.postgresql.org/docs/17/protocol-replication.html).

**Test result:** Failed before reaching source authentication:

```text
connection to server at "db.kfyztavryuuwagsfrtrs.supabase.co"
(2406:da1a:6b0:f61b:5f59:6309:7556:b936), port 5432 failed:
Network is unreachable
```

DNS resolved the direct endpoint, but the container has no usable route to that IPv6 destination. This result does not establish whether the OVH host itself has an IPv6 route. No source connection, slot, publication, or initial copy was established by the test.

**Host route check — completed:** In the OVH host's MobaXterm SSH shell, the user ran:

```bash
ip -6 route get 2406:da1a:6b0:f61b:5f59:6309:7556:b936
```

**Result:** A route exists through gateway `2001:41d0:701:1100::1` on interface `ens3`, using source address `2001:41d0:701:1100::b591`. This confirms the host has an IPv6 route; source port reachability and authentication remain untested through that route.

**Host-network test — completed:** In the same OVH MobaXterm SSH terminal, the user ran a temporary client using the host network and the locally available PostgreSQL image:

```bash
sudo docker run --rm -it --pull=never --network host --entrypoint psql postgres:17 \
  -X -W -P pager=off \
  -d 'host=db.kfyztavryuuwagsfrtrs.supabase.co port=5432 dbname=postgres user=postgres sslmode=require connect_timeout=10 replication=database' \
  -c 'IDENTIFY_SYSTEM;'
```

Enter the **Supabase database password** at the psql prompt and provide only the output or error. This launches a temporary psql client on the host's network namespace; it is removed after exit. It does not mount the existing target database's data. A successful identity result would confirm host-network connectivity and replication authentication; the actual `postgres-db` network would still need repair before subscribing. References: [Docker host networking](https://docs.docker.com/engine/network/drivers/host/) and [Docker IPv6 networking](https://docs.docker.com/engine/daemon/ipv6/).

**Host-network replication result:** Passed, as reported by the user:

```text
systemid             timeline  xlogpos      dbname
7533160901409023548   4         32/2E000000  postgres
```

This confirms direct source reachability and replication authentication through the OVH host network. The WAL position above is only identification-test evidence, not a cutover or replication-catch-up position. The actual `postgres-db` container still has the previously observed networking problem. No publication, slot, subscription, or initial data copy was created by this test.

**Container network inspection — completed:** In the same OVH MobaXterm SSH terminal, the user ran:

```bash
sudo docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
sudo docker inspect postgres-db --format 'networks={{json .NetworkSettings.Networks}} ports={{json .HostConfig.PortBindings}}'
```

**Result:** Docker client/server `29.6.1`. Container `postgres-db` is attached only to `apps_backend_net`, IPv4 `172.19.0.2/16`, gateway `172.19.0.1`, gateway priority `0`, aliases `postgres-db` and `postgres`, with no IPv6 address or gateway. Its only published database binding is `127.0.0.1:5432`. See [Docker container inspection](https://docs.docker.com/reference/cli/docker/container/inspect/). The next action is section 7. Do not create the migration publication or slot until direct replication connectivity from the actual subscriber is ready.

After routing is resolved, repeat the direct replication connection test from the **actual OVH PostgreSQL container**, using `replication=database` and `IDENTIFY_SYSTEM`, before creating a publication or a slot. The default direct endpoint uses IPv6 unless the project has the IPv4 add-on. See [Supabase external PostgreSQL replication](https://supabase.com/docs/guides/database/postgres/setup-replication-external) and [connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres).

**Read-only readiness refresh:** Source `postgres` has replication privileges; `wal_level = logical`, maximum slots and WAL senders are both `5`, and there are `0` existing slots. The only current publication is `supabase_realtime`. No migration publication or slot has been created. A fresh agent target recheck could not connect because local `127.0.0.1:15432` was refusing connections; reopen the separate MobaXterm tunnel before the next local script that connects to OVH. The successful user-run pgAdmin cleanup remains recorded above.

## 7. Prepare subscriber IPv6 networking — in progress

In the OVH MobaXterm SSH terminal, create a separate IPv6-only user-defined bridge:

```bash
sudo docker network create --driver bridge --ipv6 --ipv4=false explainit-migration-ipv6
```

Docker assigns an IPv6 ULA subnet automatically. This step creates the network only. Test a temporary psql client on it before connecting `postgres-db`. The existing application network and database port binding are the baseline to preserve when adding subscriber IPv6 access. References: [Docker network creation](https://docs.docker.com/reference/cli/docker/network/create/) and [automatic IPv6 subnets](https://docs.docker.com/engine/daemon/ipv6/#dynamic-ipv6-subnet-allocation).

**Network creation result:** Completed, as reported by the user. Network ID: `2e4bac0d87a4c84a0ba4924b1a4c36fb172e655f72b26a02b53ed70a19b697bd`. A temporary client test was performed before the subscriber attachment below.

**New-network connection test — completed:** In the same OVH MobaXterm SSH terminal, the user tested a temporary psql client attached to this new network:

```bash
sudo docker run --rm -it --pull=never --network explainit-migration-ipv6 --entrypoint psql postgres:17 \
  -X -W -P pager=off \
  -d 'host=db.kfyztavryuuwagsfrtrs.supabase.co port=5432 dbname=postgres user=postgres sslmode=require connect_timeout=10 replication=database' \
  -c 'IDENTIFY_SYSTEM;'
```

Enter the **Supabase database password** at the hidden psql prompt and share only the result or error. This is the same identity query that passed on the host network, now testing the new bridge's IPv6 egress. A successful result should identify source system `7533160901409023548` and database `postgres`; the WAL position may have advanced. See [PostgreSQL replication connection test](https://www.postgresql.org/docs/17/protocol-replication.html).

**New-network test result:** Passed, as reported by the user:

```text
systemid             timeline  xlogpos      dbname
7533160901409023548   4         32/2E000000  postgres
```

The new bridge has working IPv6 egress, source authentication, and replication-mode access. The actual subscriber test was performed after attachment, as recorded below.

**Subscriber attachment — completed:** In the same OVH MobaXterm SSH terminal, the user ran:

```bash
sudo docker network connect --gw-priority=-1 explainit-migration-ipv6 postgres-db
```

The new IPv6-only network has gateway priority `-1`; the existing IPv4 application network retains priority `0`. Docker selects IPv4 and IPv6 gateway endpoints independently, allowing the IPv4-only application network to retain the IPv4 route while the new network supplies IPv6. References: [Docker network attachment](https://docs.docker.com/reference/cli/docker/network/connect/) and [gateway selection implementation](https://github.com/moby/moby/blob/v28.0.0/libnetwork/default_gateway.go#L166-L201).

**Attachment result:** The user replied `done`; attachment is user-confirmed. The subscriber connection test below subsequently passed.

**Subscriber verification commands:** In the same OVH MobaXterm SSH terminal, the user was asked to inspect network memberships/port bindings and run the identity test inside the actual subscriber:

```bash
sudo docker inspect postgres-db --format 'networks={{json .NetworkSettings.Networks}} ports={{json .HostConfig.PortBindings}}'

sudo docker exec -it postgres-db psql -X -W -P pager=off \
  -d 'host=db.kfyztavryuuwagsfrtrs.supabase.co port=5432 dbname=postgres user=postgres sslmode=require connect_timeout=10 replication=database' \
  -c 'IDENTIFY_SYSTEM;'
```

Enter the **Supabase database password** at the psql prompt and share both outputs, excluding the password. Expect `apps_backend_net` to retain IPv4 `172.19.0.2`, the new network to show an IPv6 address/gateway, the database port binding to remain `127.0.0.1:5432`, and the identity query to identify source system `7533160901409023548`, database `postgres`.

**Subscriber connection result:** Passed, as reported by the user:

```text
systemid             timeline  xlogpos      dbname
7533160901409023548   4         32/2E000000  postgres
```

This confirms direct source connectivity and replication authentication from `postgres-db`. The user subsequently supplied the post-attachment inspection: `apps_backend_net` retains IPv4 `172.19.0.2`, gateway `172.19.0.1`, priority `0`; `explainit-migration-ipv6` supplies `fd87:3cfd:7b4c::2/64`, gateway `fd87:3cfd:7b4c::1`, priority `-1`. The published database binding remains `127.0.0.1:5432`. Persist the attachment in the OVH deployment configuration before relying on it across container recreation.

## 8. Verify target subscription permissions and capacity — completed

In the same OVH MobaXterm SSH terminal, run this read-only check on target database `explainit`:

```bash
sudo docker exec -it postgres-db psql -X -x -P pager=off -v ON_ERROR_STOP=1 -U explainit_admin -d explainit -c "
BEGIN READ ONLY;
SELECT current_database() AS database_name,
       current_user AS db_user,
       r.rolsuper AS is_superuser,
       has_database_privilege(current_user, current_database(), 'CREATE') AS database_create,
       (r.rolsuper OR pg_has_role(current_user, 'pg_create_subscription', 'USAGE')) AS subscription_role,
       current_setting('max_logical_replication_workers')::int AS logical_workers,
       current_setting('max_worker_processes')::int AS worker_processes,
       current_setting('max_sync_workers_per_subscription')::int AS sync_workers,
       current_setting('max_replication_slots')::int AS replication_slots,
       (SELECT version_num FROM public.alembic_version) AS alembic_revision,
       (SELECT count(*) FROM pg_subscription) AS existing_subscriptions
FROM pg_roles r WHERE r.rolname = current_user;
ROLLBACK;"
```

Expect database `explainit`, user `explainit_admin`, both permission checks true, revision `c03_youtube_scan_controls`, and zero existing subscriptions. Review capacity for one apply worker plus initial table-copy workers and a launcher before proceeding. The target does not need `wal_level = logical` merely to subscribe. References: [PostgreSQL subscriber configuration](https://www.postgresql.org/docs/17/logical-replication-config.html) and [subscription privileges](https://www.postgresql.org/docs/17/logical-replication-security.html).

**Result:** Passed, as reported by the user:

| Check | Result |
| --- | --- |
| Database / user | `explainit` / `explainit_admin` |
| Superuser, database CREATE, subscription role | All true |
| Logical workers / total worker processes | 4 / 8 |
| Initial-copy workers per subscription | 2 |
| Maximum replication slots | 10 |
| Alembic revision | `c03_youtube_scan_controls` |
| Existing subscriptions, cluster-wide | 0 |

This supports one apply worker plus two initial-copy workers, with room for the launcher. No target configuration change is needed for the planned initial synchronization.

## 9. Create the source publication — completed

**Source refresh before publication creation:** The agent used a read-only connection through the existing source settings to verify database/user `postgres`, database CREATE and replication privileges, `wal_level = logical`, maximum slots/senders `5` / `5`, no existing slots, and no active WAL senders. All 24 selected application tables exist and have ownership rights, SELECT access, valid primary keys, and default replica identity. At that point the only publication was `supabase_realtime`; `explainit_migration_pub` was absent.

In the OVH MobaXterm SSH terminal, use a normal source SQL connection to create the publication and verify its table count:

```bash
sudo docker exec -it postgres-db psql -X -W -P pager=off -v ON_ERROR_STOP=1 \
  -d 'host=db.kfyztavryuuwagsfrtrs.supabase.co port=5432 dbname=postgres user=postgres sslmode=require connect_timeout=10' \
  -c "
SET lock_timeout = '5s';
SET statement_timeout = '30s';
CREATE PUBLICATION explainit_migration_pub FOR TABLE
  public.admin_users, public.article_comments, public.articles,
  public.content_audit, public.content_qa_reports, public.content_research_settings,
  public.content_runs, public.content_topic_identifiers, public.content_topics,
  public.content_versions, public.discovered_trends, public.draft_prompts,
  public.email_otps, public.host_sites, public.leads,
  public.niche_keywords, public.niches, public.popular_scan_settings,
  public.public_api_keys, public.tokens, public.trend_ideas,
  public.trend_items, public.trend_videos, public.volume_points
WITH (publish = 'insert, update, delete, truncate');
SELECT pubname, count(*) AS published_tables
FROM pg_publication_tables
WHERE pubname = 'explainit_migration_pub'
GROUP BY pubname;"
```

Enter the **Supabase database password** when prompted. Expected: `CREATE PUBLICATION` and `explainit_migration_pub | 24`. The explicit list excludes `alembic_version`, `schema_migrations`, and Supabase-managed tables. This defines the source table selection; data copying starts when the subscription is created. A duplicate-name error must be reviewed rather than replacing an existing publication. References: [PostgreSQL CREATE PUBLICATION](https://www.postgresql.org/docs/17/sql-createpublication.html) and [publication table membership](https://www.postgresql.org/docs/17/view-pg-publication-tables.html).

**Result:** Completed. The user reported `24` published tables. The agent independently verified through a read-only source connection that the publication contains the exact reviewed 24-table set, excludes migration metadata, is not `FOR ALL TABLES`, and publishes insert/update/delete/truncate operations. The subscription and slot were created subsequently in section 10.

## 10. Start the subscription and initial copy — completed

The ignored `.env` in this migration repository now also contains `SOURCE_REPLICATION_DATABASE_URL`, using the user-confirmed direct source host/user/database and the existing source password, with SSL required. Existing `SOURCE_DATABASE_URL`, target settings, and the FastAPI repository configuration were preserved. The source assessment continues to use its working pooler URL; the target replication worker will use the direct source URL.

**Tunnel restored:** An earlier target probe returned `Connection refused` at local `15432`. The user restarted the saved MobaXterm tunnel; the agent then connected read-only and verified database/user `explainit` / `explainit_admin`, ICU `en-US`, and zero subscriptions. Keep this tunnel running while using the local helper.

The native replication connection from `postgres-db` to Supabase already passed. This local SSH forward is for starting and monitoring the subscription from the migration repository; replication will run directly between the two PostgreSQL servers.

**Read-only helper preflight — passed:** The agent ran:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\start_replication.py' --check
```

Result: exact 24-table publication, all target application tables empty, and matching revision `c03_youtube_scan_controls`. The source has 5 free slots and 5 free WAL senders; the target has 4 free logical workers, 7 free background worker slots, and origin capacity 10. The planned apply worker plus two configured initial-copy workers require capacity 3.

The [subscription helper](start_replication.py) reads this repository's ignored `.env`, checks source and target using read-only transactions, and defaults to checking only. It refuses existing subscriptions/slots, unexpected publication membership, populated application tables, wrong revisions/endpoints, or insufficient capacity. Its errors omit driver messages, SQL, DSNs, and passwords.

**Executed by the user:** In **local Windows PowerShell**, from this migration repository:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\start_replication.py' --apply
```

After rechecking all prerequisites, this runs `CREATE SUBSCRIPTION` alone with autocommit enabled on OVH `explainit`: subscription `explainit_migration_sub`, source slot `explainit_migration_slot`, publication `explainit_migration_pub`, `copy_data = true`, `create_slot = true`, `enabled = true`, `streaming = off`, and `binary = false`. It reuses the source password from `.env` without putting it in the shell command. Reference: [PostgreSQL CREATE SUBSCRIPTION](https://www.postgresql.org/docs/17/sql-createsubscription.html).

Expected final lines:

```text
Created explainit_migration_sub; initial copy started for 24 tables using slot explainit_migration_slot.
Next: monitor table synchronization and replication lag; this does not complete cutover.
```

**Apply result:** Successful. The user reported the exact creation messages above. The agent then verified the enabled subscription, expected slot/publication names, active `pgoutput` source slot, and live apply/synchronization workers using read-only queries. Do not rerun the initialization helper; monitoring is the next action. Keep FastAPI and other application writers using Supabase during the initial copy.

## 11. Monitor initial synchronization — next step

The first agent read-only monitoring sample found:

| Check | Result |
| --- | --- |
| Table states | 19 `r` (ready), 2 `d` (copying), 3 `i` (initializing) |
| Active table-copy workers | `trend_videos`, `trend_ideas` |
| Remaining initialized tables | `admin_users`, `content_research_settings`, `content_topic_identifiers` |
| Apply / synchronization errors | 0 / 0 |
| Subscription | Enabled, expected publication and source slot |
| Source slot | Active, logical `pgoutput`, database `postgres`, WAL status `reserved` |
| Apply connection | Streaming |
| Source acknowledged WAL distance / retained WAL | 0 / 56 bytes in this sample |

The acknowledged WAL distance is not a measure of unfinished initial table copies. This sample still had five tables not ready. PostgreSQL table states are documented in [pg_subscription_rel](https://www.postgresql.org/docs/17/catalog-pg-subscription-rel.html); worker/error counters are in [subscription statistics](https://www.postgresql.org/docs/17/monitoring-stats.html), and source WAL positions are in [replication slots](https://www.postgresql.org/docs/17/view-pg-replication-slots.html).

To repeat the read-only status check from **local PowerShell** in this migration repository, with the administration tunnel running:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\monitor_replication.py'
```

The monitor uses read-only connections and saves `replication_status.json` with table states, worker/error status, and source-slot metadata. It does not read table contents or subscription connection strings. It separates initial table-copy completion, replication activity, and cumulative/new errors. All 24 tables must be `r` before moving to data validation. Its health flag requires the expected enabled subscription, a live apply worker, an active valid source slot, and either zero cumulative errors or no increase between comparable samples within 15 minutes. Missing, stale or reset statistics make the error delta unknown.

**Latest saved result (2026-10-22, 06:17 IST):** 23 tables are `r`; `trend_videos` remains `d`. The apply worker and one synchronization worker are live. Cumulative apply errors are `0`; synchronization errors increased from `1` to `2` across samples. The main source slot is active and `reserved`, with acknowledged WAL distance `0` bytes. This does not establish that `trend_videos` has finished copying. A further read-only COPY progress check found an active retry with no blocking database PIDs; the failure cause is still unknown.

Error counters are cumulative. A nonzero count remains visible after a successful retry; its increase here is evidence of another error between samples. Preserve these counters and use worker state, table readiness, subsequent samples, and the actual error log to assess recovery. The monitor now reports cumulative counts and new errors separately, so historical errors alone do not permanently mark recovered replication unhealthy. A successful status check still does not establish data equality.

**Diagnostic command used:** In the **OVH MobaXterm SSH terminal**, collect only filtered error and synchronization-worker lines:

```bash
sudo docker logs --since 20m postgres-db 2>&1 | grep -E 'ERROR:|FATAL:|synchronization worker.*(exited|started|finished)' | tail -n 30
```

**Log result:** The user supplied repeated `could not receive data from WAL stream: ERROR: canceling statement due to statement timeout` errors for `trend_videos`, followed by automatic copy-worker retries approximately every 124 seconds. The underlying source timeout was confirmed as `120000` ms using a read-only settings query; the existing subscription connection has no startup-options override. Proceed to section 12. Data equality and application behavior still need validation after initial synchronization; final validation and sequence reconciliation occur around the cutover write pause.

## 12. Remove the source timeout for this subscription — completed

The large initial COPY is exceeding the source session's two-minute statement timeout. Supabase documents this default and permits session-specific overrides on direct connections: [Supabase timeouts](https://supabase.com/docs/guides/database/postgres/timeouts).

The [repair helper](repair_replication_timeout.py) reads the existing target subscription's connection privately and sets its startup options to `-c statement_timeout=0` using `ALTER SUBSCRIPTION ... CONNECTION`. It checks the target, subscription, source endpoint and existing options, preserves the remaining connection parameters, and suppresses connection strings/passwords in output. Default execution is read-only; `--apply` performs the change. References: [PostgreSQL ALTER SUBSCRIPTION](https://www.postgresql.org/docs/17/sql-altersubscription.html) and [libpq startup options](https://www.postgresql.org/docs/17/libpq-connect.html#LIBPQ-PARAMKEYWORDS).

**Executed by the user:** In **local Windows PowerShell**, from this migration repository with the administration tunnel running:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\repair_replication_timeout.py' --apply
```

The setting applies only to this subscription's new source sessions. A copy already running with the old timeout can fail once more before its automatic retry picks up the change. Existing table synchronization state is retained. The initial-creation helper was also amended to include this override for future subscriptions; do not rerun it for the existing subscription.

**Result:** Applied successfully by the user. Output confirmed `statement_timeout=0` was configured and verified, with all other connection fields preserved. After the local tunnel became available again, the agent independently ran the helper with `--check`; it reported that the override was already configured and made no change. No source-wide setting was changed.

## 13. Check recovery and finish the initial copy — initial copy completed

Fresh read-only samples after the fix found:

| Check | Result |
| --- | --- |
| Table states | 23 ready; `trend_videos` copying |
| COPY progress | Increased from 2.7 MB / 327 rows to 12.6 MB / 1,363 rows |
| New errors between fresh samples | Apply `0`, synchronization `0` |
| Historical errors | Apply `0`, synchronization `462`; preserved cumulative counters |
| Replication | Live apply and copy workers; source slot active and valid |
| Validation readiness | False; the last table must finish first |

The latest check at 22:25 IST on 2026-10-22 still showed zero new errors, more than three minutes after the observed copy worker started. The copy continued beyond the former two-minute timeout window.

The monitor now includes COPY bytes and rows processed in both terminal output and `replication_status.json`. It labels the total unknown when PostgreSQL does not supply it, and does not calculate a completion percentage from that value.

**Immediate next step:** Wait about five minutes, then run this in **local PowerShell** from the migration repository, with the administration tunnel running:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\monitor_replication.py'
```

**Follow-up result (2026-10-25, 00:54 IST):** The agent's live read-only monitor confirmed all 24 tables are `r` and `initial_copy_complete = true`. However, `replication_running = false`, with no apply or synchronization workers; cumulative apply errors are 20,328 and synchronization errors remain 462. The old saved sample was too old to calculate a fresh counter delta. Initial copying has finished, but ongoing replication must recover before validation or cutover.

## 14. Diagnose the ongoing apply failure — error identified

The live target subscription remains enabled, references the expected slot/publication, has `disable_on_error = false`, and has no transaction-skip LSN configured. The target revision remains `c03_youtube_scan_controls`; worker settings remain 4 logical workers and 8 total worker processes. The source slot is inactive, with about 1.91 GB of unacknowledged/retained WAL, status `reserved`, and no invalidation. These checks do not identify the error cause.

The local administration tunnel was initially unavailable; after the user's retry, the agent connected and obtained the live result above. The target sends PostgreSQL errors to Docker stderr (`logging_collector = off`), so the next diagnostic is the filtered container log.

In the **OVH MobaXterm SSH terminal**, run:

```bash
sudo docker logs --since 10m postgres-db 2>&1 | grep -E 'ERROR:|FATAL:' | tail -n 10
```

**Log result:** The user reported `ERROR: duplicate key value violates unique constraint "discovered_trends_phrase_key"`. This identifies the current conflict in `discovered_trends`; the earlier `trend_videos` COPY timeout was already fixed. The user prefers to separate trend/YouTube features from this project rather than retain those tables in ongoing replication. Proceed with the prepared scope change in section 15. Validation, source catch-up, sequence reconciliation and cutover remain outstanding.

## 15. Exclude trend/YouTube tables from ongoing replication — superseded after reported OVH switch

**Do not run the `--apply` command below.** After this step was prepared, the user reported that the application already uses OVH. The command's final ENABLE is unsuitable until the live cutover has been assessed; `--apply` is now blocked in code. The manifest subsequently observed at `replication_scope.json` records the 19-table scope, verified at 2026-10-25 01:15 IST. Follow section 16.

Planned exclusions: `public.discovered_trends`, `public.trend_ideas`, `public.trend_items`, `public.trend_videos`, and `public.volume_points`. Keep the other 19 application tables, including shared `niches`, `niche_keywords` and settings. All physical tables and rows remain on both databases; excluded tables will stop receiving replicated changes.

The [exclusion helper](exclude_trend_replication.py) defaults to a read-only check. With `--apply`, it removes the five tables from the source publication, refreshes the enabled target subscription with `copy_data = false`, then disables and waits for workers to exit. It verifies exactly 19 ready relations, saves `replication_scope.json`, and re-enables the subscription so a fresh worker ignores excluded relations. The existing source slot, connection parameters and error counters are preserved. It does not skip an entire source transaction. References: [ALTER PUBLICATION](https://www.postgresql.org/docs/17/sql-alterpublication.html), [ALTER SUBSCRIPTION](https://www.postgresql.org/docs/17/sql-altersubscription.html), and [PostgreSQL apply filtering](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/backend/replication/logical/worker.c#L449-L469).

Checks require the confirmed endpoints, PostgreSQL versions/revisions, unchanged publication properties, a valid existing slot, completed initial copies, and no retained-table foreign key pointing into an excluded table. It supports resuming an interrupted 24-to-19 scope change and suppresses credentials/SQL in errors. If verification fails after disabling, it leaves that last confirmed state for review rather than enabling unverified scope.

**Prepared verification:** 18 offline exclusion checks and six offline scope-manifest checks passed. The live read-only source preflight passed with all 24 original published tables, a valid slot, matching revision and no retained-table FK into the excluded group. The full live preflight could not reach the local target tunnel; it has not changed either database.

From **local PowerShell** in this migration repository, with the saved local-port-15432 tunnel running:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\exclude_trend_replication.py' --apply
```

After it succeeds, `monitor_replication.py` reads the applied manifest and expects 19 ready tables. Until then it continues to expect the original 24 and checks source publication membership against that scope. The original schema comparison remains unchanged as evidence. The 19-table scope contains 13 owned sequences to reconcile at cutover, excluding the five owned by the separated feature.

**Result:** The applied manifest was subsequently observed, and a live apply worker was seen after the user's OVH-switch disclosure. Earlier assumptions that OVH was still receiving no application writes no longer apply. Do not re-enable forward replication or make corrective data writes before reviewing the actual cutover.

## 16. Protect the reported live OVH database — immediate step

The user reported: "i have already switch the db to ovh". A read-only target check then found `explainit_migration_sub` enabled with a live apply PID. Incoming replication can update rows that were changed locally on OVH; see [PostgreSQL replication conflicts](https://www.postgresql.org/docs/17/logical-replication-conflicts.html). The exclusion helper's `--apply` path is now blocked before configuration or database access.

In pgAdmin Query Tool connected to **OVH `explainit`**, with autocommit on, run this command by itself:

```sql
ALTER SUBSCRIPTION explainit_migration_sub DISABLE;
```

Verify that the subscription is disabled and all its workers exit before further migration changes. DISABLE stops the replication worker after the command commits: [ALTER SUBSCRIPTION](https://www.postgresql.org/docs/17/sql-altersubscription.html). Preserve both databases, the subscription, source slot, and error history while assessing reconciliation.

**Confirmed user facts:** OVH is live, and Supabase writers have stopped. The user gave the switch date as "yesterday", recorded as approximately 2026-10-24 in the client's timezone; the exact time and the time source writers stopped are not supplied. Read-only post-switch audits are recorded in section 17.

**Result:** The user's pgAdmin attempt returned `subscription does not exist`. The agent verified that subscription OID `16811` still existed, enabled, in OVH `explainit` with the expected slot/publication and a live worker; the user's Query Tool was therefore in a different database context. Using that verified target connection, the agent executed the same authorized `ALTER SUBSCRIPTION ... DISABLE` command with autocommit, then confirmed `subenabled = false` and zero live replication workers. No application rows were modified.

**Source boundary after disabling (2026-10-25, 01:24 IST):** The existing source slot was inactive and valid (`reserved`, no invalidation). `confirmed_flush_lsn` and the observed source current WAL position both equaled `34/13000060`, giving zero unacknowledged WAL. This position is audit evidence, not proof of row equality or preservation of all OVH-only changes made before incoming replication stopped. The physical tables, existing data, subscription, slot and error counters remain preserved.

The disabled state is now intentional. Do not use a monitor's `replication_running = false` as a reason to re-enable it on the live OVH database. Next, review retained-table data and the 13 owned sequences without writes, then choose any corrective action from the actual findings.

## 17. Audit live OVH data and ID sequences — read-only checks completed

The agent ran [live_target_audit.py](live_target_audit.py) against both databases using independent read-only repeatable-read snapshots. It checked live column types and primary keys, compared canonical row values using SHA-256, and verified that incoming replication stayed disabled with no live workers. Text-key comparisons use `COLLATE "C"` to avoid source/target ICU ordering differences. Evidence: [live_target_audit.json](live_target_audit.json).

| Check | Result |
| --- | --- |
| Retained tables compared | 19 |
| Source / OVH rows | 431 / 433 |
| Source primary keys missing on OVH | 0 |
| Tables with identical canonical rows | 18 |
| `leads` | Six shared keys with changed values; two additional OVH keys |
| Actual foreign-key orphan checks | Nine constraints, zero orphan rows |
| Index health | All 51 retained-table indexes valid, ready and live |
| Owned ID sequences | 13 checked; eight behind the occupied ID range |

A field-level follow-up on `leads` found differences only in `status`, `internal_notes` and `updated_at` for the six shared rows; other fields matched. Evidence: [leads_field_audit.json](leads_field_audit.json). Preserve these OVH changes and the two additional leads while interpreting them against production activity; no row repair was performed. Foreign-key and index evidence is in [live_target_integrity.json](live_target_integrity.json).

These checks describe current snapshots, not a reconstruction of every change during the earlier overlap between OVH writes and incoming replication. JSON comparison is canonical-value comparison rather than byte-for-byte JSON formatting. No row contents, per-row keys or hashes are saved in the reports; sequence observations contain aggregate table maxima and counter positions.

## 18. Advance the eight behind ID sequences — next step

The read-only correction preflight checked all 13 retained-owned sequences. Eight require upward adjustment:

| Sequence | Observed next candidate | Existing maximum | Proposed next |
| --- | ---: | ---: | ---: |
| `articles_id_seq` | 1 | 62 | 63 |
| `content_audit_id_seq` | 1 | 108 | 109 |
| `content_topic_identifiers_id_seq` | 1 | 100 | 101 |
| `draft_prompts_id_seq` | 1 | 3 | 4 |
| `host_sites_id_seq` | 2 | 3 | 4 |
| `niche_keywords_id_seq` | 1 | 212 | 213 |
| `niches_id_seq` | 1 | 36 | 37 |
| `public_api_keys_id_seq` | 1 | 5 | 6 |

An upcoming candidate in an occupied range indicates potential duplicate-ID failures; gaps mean the very next insert is not guaranteed to collide. The five remaining sequences passed the observational check. Evidence: [target_sequence_repair_plan.json](target_sequence_repair_plan.json).

The [sequence repair helper](repair_target_sequences.py) defaults to a read-only plan. With `--apply`, it connects only to OVH and uses one bounded transaction. It locks the 13 owner tables, then locks each sequence using a same-owner metadata command. Under those locks it rechecks ownership, defaults, parameters, current positions and table maxima, and transactionally restarts only counters still behind. No application rows or existing IDs are changed; no counter moves backward. Incoming replication must remain disabled. The target was checked for enabled event triggers and none were found.

Writes to the owner tables and sequence allocations may briefly wait; ordinary table reads can continue. Lock timeout is five seconds and transaction timeout is 45 seconds. Guard failures or pre-commit errors roll back pending restarts. See [PostgreSQL transactional sequence restart](https://www.postgresql.org/docs/17/sql-altersequence.html) and [table locking](https://www.postgresql.org/docs/17/explicit-locking.html).

**Checks:** Seven offline repair scenarios passed, including monotonic planning, idempotency, exhaustion rejection, read-only default, mutation order, rollback and secret suppression. The live `--check` preflight passed and saved the plan above. No sequence changes have been applied by the agent.

With the local-port-15432 administration tunnel running, execute in **local PowerShell** from this migration repository:

```powershell
$fastApiRepo = 'E:\projects\blogging-system\ei-fastapi-be'
& "$fastApiRepo\.venv\Scripts\python.exe" '.\repair_target_sequences.py' --apply
```

The helper recomputes all values under locks and verifies all 13 sequences after committing. If commit acknowledgement is uncertain, use `--check` before retrying. **Result:** Prepared; user application and verification of corrected positions are pending.

Sequence reconciliation remains a cutover requirement.
