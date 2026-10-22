"""Check logical replication readiness; --apply creates the subscription and starts copying.

Default and --check perform read-only checks. Secrets come from the ignored .env;
SQL, connection strings, driver messages and exception contexts are never printed.
No subscription or replication slot is dropped or reused.
"""

import argparse
import json
import sys

from dotenv import dotenv_values
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from prepare_target_schema import PreparationError, REVISION, WORKSPACE, load_target


PUBLICATION = "explainit_migration_pub"
SUBSCRIPTION = "explainit_migration_sub"
SLOT = "explainit_migration_slot"
PROJECT = "kfyztavryuuwagsfrtrs"
DIRECT_HOST = f"db.{PROJECT}.supabase.co"
METADATA_TABLES = {("public", "alembic_version"), ("public", "schema_migrations")}


def require(condition, message):
    if not condition:
        raise PreparationError(message)


def connection_parameters(url):
    require(url.drivername.startswith("postgresql") and url.host,
            "Expected PostgreSQL URLs with explicit hosts.")
    # Prevent query parameters from silently overriding the checked endpoint/user.
    forbidden = {"host", "hostaddr", "port", "dbname", "database", "user", "password",
                 "service", "servicefile", "passfile"}
    require(not (forbidden & url.query.keys()), "URL query overrides are not permitted.")
    parameters = url.translate_connect_args(username="user", database="dbname")
    parameters.update(url.query)
    parameters.pop("replication", None)
    parameters["connect_timeout"] = 10
    return parameters


def configuration():
    settings = dotenv_values(WORKSPACE / ".env", interpolate=False)
    source = make_url(settings["SOURCE_DATABASE_URL"])
    direct = make_url(settings["SOURCE_REPLICATION_DATABASE_URL"])
    target = load_target()
    require(source.database == "postgres", "The source database must be postgres.")
    require((source.username == f"postgres.{PROJECT}") or
            (source.username == "postgres" and source.host == DIRECT_HOST),
            "The source URL must identify the confirmed Supabase project.")
    require((direct.host, direct.port or 5432, direct.database, direct.username)
            == (DIRECT_HOST, 5432, "postgres", "postgres"),
            "The worker URL must use the confirmed direct Supabase endpoint and postgres role.")
    require(source.password and direct.password == source.password,
            "Source query and replication URLs must use the same nonempty password.")
    require(direct.query.get("sslmode") == "require", "The worker URL must require SSL.")
    source_parameters, target_parameters = connection_parameters(source), connection_parameters(target)
    worker_parameters = connection_parameters(direct)
    worker_parameters["sslmode"] = "require"
    worker_parameters["application_name"] = SUBSCRIPTION
    # Supabase's default 120-second statement timeout can cancel initial COPY.
    # This startup setting applies only to the subscription's source sessions.
    require(worker_parameters.get("options", "") in ("", "-c statement_timeout=0"),
            "Unexpected replication startup options; review them before starting replication.")
    worker_parameters["options"] = "-c statement_timeout=0"
    worker_dsn = make_dsn(**worker_parameters)
    report = json.loads((WORKSPACE / "schema_comparison.json").read_text(encoding="utf-8"))
    names = report["application_tables"]
    require(report.get("application_schema_matches") is True,
            "The saved application schema comparison must pass before replication.")
    require(isinstance(names, list) and len(names) == 24 and len(set(names)) == 24,
            "The saved schema comparison must identify exactly 24 distinct application tables.")
    require(all(isinstance(name, str) and name.startswith("public.")
                and name.count(".") == 1 for name in names),
            "Expected public application table names in the saved comparison.")
    tables = {tuple(name.split(".", 1)) for name in names}
    require(not (tables & METADATA_TABLES), "Migration metadata must not be published.")
    return source_parameters, target_parameters, worker_dsn, tables


def connect_read_only(parameters):
    connection = psycopg2.connect(**parameters)
    connection.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL search_path = pg_catalog")
        cursor.execute("SET LOCAL statement_timeout = '30s'")
        cursor.execute("SET LOCAL lock_timeout = '5s'")
    return connection


def revision(cursor, label):
    cursor.execute("SELECT version_num FROM public.alembic_version ORDER BY version_num")
    require([row[0] for row in cursor.fetchall()] == [REVISION],
            f"{label} must be at the expected Alembic revision {REVISION}.")


def check_target(connection, tables):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), current_user, datlocprovider, datlocale,
                   r.rolsuper, current_setting('server_version_num')::int
            FROM pg_database d JOIN pg_roles r ON r.rolname = current_user
            WHERE d.datname = current_database()
        """)
        database, user, provider, locale, superuser, version = cursor.fetchone()
        require((database, user, provider, locale) == ("explainit", "explainit_admin", "i", "en-US")
                and superuser and 170000 <= version < 180000,
                "Target must be PostgreSQL 17 explainit, ICU en-US, as superuser explainit_admin.")
        revision(cursor, "Target")
        cursor.execute("SELECT subname FROM pg_subscription WHERE subdbid = "
                       "(SELECT oid FROM pg_database WHERE datname = current_database())")
        require(not cursor.fetchall(), "Target already has a subscription; inspect it before retrying.")
        cursor.execute("""
            SELECT n.nspname, c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
        """)
        actual = set(cursor.fetchall()) - METADATA_TABLES
        require(actual == tables, "Target application table names differ from the reviewed 24-table set.")
        for schema, table in sorted(tables):
            cursor.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {} LIMIT 1)").format(
                sql.Identifier(schema, table)))
            require(not cursor.fetchone()[0], f"Target application table {schema}.{table} is not empty.")
        cursor.execute("""
            SELECT current_setting('max_sync_workers_per_subscription')::int,
                   current_setting('max_logical_replication_workers')::int,
                   current_setting('max_worker_processes')::int,
                   current_setting('max_replication_slots')::int,
                   (SELECT count(*) FROM pg_stat_activity
                    WHERE backend_type = 'logical replication worker'),
                   (SELECT count(*) FROM pg_replication_origin),
                   (SELECT count(*) FROM pg_stat_activity WHERE backend_type NOT IN (
                       'client backend', 'autovacuum launcher', 'autovacuum worker',
                       'background writer', 'checkpointer', 'standalone backend',
                       'startup', 'walreceiver', 'walsender', 'walwriter', 'archiver',
                       'walsummarizer'))
        """)
        sync, logical, workers, origins, used_logical, used_origins, used_workers = cursor.fetchone()
        required = 1 + sync
        require(sync >= 1, "Target must allow at least one table synchronization worker.")
        require(logical - used_logical >= required and workers - used_workers >= required
                and origins - used_origins >= required,
                "Target needs capacity for the apply worker and all configured synchronization workers.")
        return {"sync_workers": sync, "required_workers": required,
                "free_logical_workers": logical - used_logical,
                "free_background_workers": workers - used_workers,
                "free_origin_capacity": origins - used_origins}


def check_source(connection, tables, required):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), current_user, r.rolsuper, r.rolreplication,
                   r.rolbypassrls, current_setting('server_version_num')::int,
                   current_setting('wal_level')
            FROM pg_roles r WHERE r.rolname = current_user
        """)
        database, user, superuser, replication, bypass_rls, version, wal_level = cursor.fetchone()
        require(database == "postgres" and user == "postgres" and (superuser or replication)
                and 170000 <= version < 180000 and wal_level == "logical",
                "Source must be PostgreSQL 17 postgres, with the confirmed replication-capable postgres role.")
        revision(cursor, "Source")
        cursor.execute("""
            SELECT puballtables, pubinsert, pubupdate, pubdelete, pubtruncate
            FROM pg_publication WHERE pubname = %s
        """, (PUBLICATION,))
        require(cursor.fetchone() == (False, True, True, True, True),
                "Expected explicit source publication with insert/update/delete/truncate enabled.")
        cursor.execute("""
            SELECT count(*) FROM pg_publication_namespace pn
            JOIN pg_publication p ON p.oid = pn.pnpubid WHERE p.pubname = %s
        """, (PUBLICATION,))
        require(cursor.fetchone()[0] == 0, "Publication must not implicitly include entire schemas.")
        cursor.execute("""
            SELECT n.nspname, c.relname, pr.prattrs IS NULL, pr.prqual IS NULL,
                   has_table_privilege(c.oid, 'SELECT'), has_schema_privilege(n.oid, 'USAGE'),
                   c.relrowsecurity, c.relforcerowsecurity, c.relowner = r.oid
            FROM pg_publication_rel pr JOIN pg_publication p ON p.oid = pr.prpubid
            JOIN pg_class c ON c.oid = pr.prrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_roles r ON r.rolname = current_user WHERE p.pubname = %s
        """, (PUBLICATION,))
        published = cursor.fetchall()
        require({(row[0], row[1]) for row in published} == tables and len(published) == 24,
                "Publication table names must exactly match the reviewed 24 application tables.")
        require(all(all(row[2:6]) for row in published),
                "Publication needs all columns, no row filters, and source SELECT/schema privileges.")
        require(all(not row[6] or superuser or bypass_rls or (row[8] and not row[7])
                    for row in published), "Source role must bypass RLS for every published table.")
        cursor.execute("SELECT 1 FROM pg_replication_slots WHERE slot_name = %s", (SLOT,))
        require(cursor.fetchone() is None, "Expected source slot already exists; inspect it before retrying.")
        cursor.execute("""
            SELECT current_setting('max_replication_slots')::int
                       - (SELECT count(*) FROM pg_replication_slots),
                   current_setting('max_wal_senders')::int
                       - (SELECT count(*) FROM pg_stat_replication)
        """)
        slots, senders = cursor.fetchone()
        require(slots >= required and senders >= required,
                "Source needs free slots and WAL senders for apply plus all configured sync workers.")
        return {"free_slots": slots, "free_wal_senders": senders}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Run read-only preflight (the default).")
    mode.add_argument("--apply", action="store_true", help="Create the subscription and start the initial copy.")
    arguments = parser.parse_args()
    source = target = None
    phase = "configuration"
    apply_attempted = False
    try:
        source_parameters, target_parameters, worker_dsn, tables = configuration()
        phase = "target connection"
        target = connect_read_only(target_parameters)
        phase = "target preflight"
        target_capacity = check_target(target, tables)
        phase = "source connection"
        source = connect_read_only(source_parameters)
        phase = "source preflight"
        source_capacity = check_source(source, tables, target_capacity["required_workers"])
        source.close()
        source = None
        print(f"Preflight passed: {PUBLICATION}, exactly 24 application tables, target empty, revision {REVISION}.")
        print("Capacity: " + json.dumps({"source": source_capacity, "target": target_capacity}))
        if not arguments.apply:
            print("Read-only check complete; no subscription or slot created. Use --apply to start the initial copy.")
            return 0
        target.rollback()
        target.set_session(isolation_level="READ COMMITTED", readonly=False, autocommit=True)
        phase = "subscription creation"
        with target.cursor() as cursor:
            statement = sql.SQL("CREATE SUBSCRIPTION {} CONNECTION {} PUBLICATION {} "
                                "WITH (copy_data = true, create_slot = true, enabled = true, "
                                "streaming = off, binary = false, slot_name = {})").format(
                sql.Identifier(SUBSCRIPTION), sql.Literal(worker_dsn),
                sql.Identifier(PUBLICATION), sql.Literal(SLOT))
            apply_attempted = True
            cursor.execute(statement)
        print(f"Created {SUBSCRIPTION}; initial copy started for 24 tables using slot {SLOT}.")
        print("Next: monitor table synchronization and replication lag; this does not complete cutover.")
        return 0
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except psycopg2.Error as error:
        # Even message_primary may echo conninfo fragments. Only type/code and our
        # fixed phase are emitted; no pgerror, SQL, detail, context or parameters.
        print(f"Replication failed during {phase} ({type(error).__name__}; "
              f"SQLSTATE {error.pgcode or 'unavailable'}).", file=sys.stderr)
        if "connection" in phase:
            print("Check the configured endpoints and the local target SSH tunnel.", file=sys.stderr)
        if apply_attempted:
            print("Inspect the source slot and target subscription before retrying; "
                  "a failed CREATE can leave a source slot. Nothing was dropped or reused.", file=sys.stderr)
        return 1
    except (SQLAlchemyError, KeyError, TypeError, ValueError, OSError):
        print("Replication configuration failed. Check .env and the reviewed schema comparison report.", file=sys.stderr)
        return 1
    finally:
        for connection in (source, target):
            if connection is not None:
                connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
