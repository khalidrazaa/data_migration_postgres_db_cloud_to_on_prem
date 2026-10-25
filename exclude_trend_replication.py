"""Inspect the trend/YouTube replication scope after the reported OVH switch.

Default and --check are read-only. --apply is blocked because OVH has been
reported as the application database. Resuming forward replication requires
reviewing the actual cutover and live OVH writes first.
"""

import argparse
import sys
import time

import psycopg2
from psycopg2 import sql
from sqlalchemy.exc import SQLAlchemyError

from migration_scope import EXCLUDED_TABLES, write_applied_scope
from prepare_target_schema import PreparationError, REVISION
from repair_replication_timeout import TIMEOUT_OPTIONS, checked_parameters
from start_replication import (
    METADATA_TABLES, PUBLICATION, SLOT, SUBSCRIPTION,
    configuration, connect_read_only, require, revision,
)


OVH_REPORTED_LIVE = True


def application_tables(cursor):
    cursor.execute("""
        SELECT n.nspname, c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    """)
    return set(cursor.fetchall()) - METADATA_TABLES


def check_foreign_keys(cursor, retained):
    cursor.execute("""
        SELECT cn.nspname, child.relname, pn.nspname, parent.relname
        FROM pg_constraint con
        JOIN pg_class child ON child.oid = con.conrelid
        JOIN pg_namespace cn ON cn.oid = child.relnamespace
        JOIN pg_class parent ON parent.oid = con.confrelid
        JOIN pg_namespace pn ON pn.oid = parent.relnamespace
        WHERE con.contype = 'f'
    """)
    require(not any((cs, child) in retained and (ps, parent) in EXCLUDED_TABLES
                    for cs, child, ps, parent in cursor.fetchall()),
            "A retained table references an excluded table; review the exclusion boundary first.")


def check_source(connection, original, retained):
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user, "
                       "current_setting('server_version_num')::int")
        database, user, version = cursor.fetchone()
        require(database == 'postgres' and user == 'postgres'
                and 170000 <= version < 180000,
                "Source must be the confirmed PostgreSQL 17 postgres database and role.")
        revision(cursor, "Source")
        require(application_tables(cursor) == original,
                "Source application table names differ from the reviewed original scope.")
        cursor.execute("""
            SELECT p.puballtables, p.pubinsert, p.pubupdate, p.pubdelete,
                   p.pubtruncate, (p.pubowner = r.oid OR r.rolsuper)
            FROM pg_publication p JOIN pg_roles r ON r.rolname = current_user
            WHERE p.pubname = %s
        """, (PUBLICATION,))
        require(cursor.fetchone() == (False, True, True, True, True, True),
                "The expected explicit publication and its ownership/operations must be unchanged.")
        cursor.execute("""
            SELECT count(*) FROM pg_publication_namespace pn
            JOIN pg_publication p ON p.oid = pn.pnpubid WHERE p.pubname = %s
        """, (PUBLICATION,))
        require(cursor.fetchone()[0] == 0,
                "The migration publication must not include entire schemas.")
        cursor.execute("""
            SELECT n.nspname, c.relname, pr.prattrs IS NULL, pr.prqual IS NULL
            FROM pg_publication_rel pr JOIN pg_publication p ON p.oid = pr.prpubid
            JOIN pg_class c ON c.oid = pr.prrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE p.pubname = %s
        """, (PUBLICATION,))
        published = cursor.fetchall()
        members = {(row[0], row[1]) for row in published}
        require(members in (original, retained) and len(published) == len(members)
                and all(row[2] and row[3] for row in published),
                "Source publication must contain exactly the original 24 or agreed 19 tables, without filters.")
        cursor.execute("""
            SELECT plugin, slot_type, database, wal_status, invalidation_reason
            FROM pg_replication_slots WHERE slot_name = %s
        """, (SLOT,))
        slot = cursor.fetchone()
        require(slot is not None and slot[:3] == ('pgoutput', 'logical', 'postgres')
                and slot[3] in ('reserved', 'extended') and slot[4] is None,
                "The existing migration slot must remain valid and use pgoutput for postgres.")
        check_foreign_keys(cursor, retained)
        return members


def check_target(connection, original, retained):
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), current_user,
                   current_setting('server_version_num')::int, r.rolsuper
            FROM pg_roles r WHERE r.rolname = current_user
        """)
        database, user, version, superuser = cursor.fetchone()
        require((database, user) == ('explainit', 'explainit_admin')
                and superuser and 170000 <= version < 180000,
                "Target must be PostgreSQL 17 explainit as superuser explainit_admin.")
        revision(cursor, "Target")
        require(application_tables(cursor) == original,
                "All original target application tables must remain present.")
        cursor.execute("""
            SELECT oid, subname, subenabled, subslotname, subpublications,
                   subskiplsn::text, subconninfo
            FROM pg_subscription WHERE subdbid =
                (SELECT oid FROM pg_database WHERE datname = current_database())
        """)
        subscriptions = cursor.fetchall()
        require(len(subscriptions) == 1,
                "The target database must have only the expected migration subscription.")
        identifier, name, enabled, slot, publications, skip_lsn, conninfo = subscriptions[0]
        require(name == SUBSCRIPTION and slot == SLOT and publications == [PUBLICATION]
                and skip_lsn == '0/0',
                "Subscription name, slot, publication or transaction-skip state is unexpected.")
        # REFRESH uses this stored connection, not the helper's source query URL.
        # Parse it privately and never include driver diagnostics or conninfo in output.
        require(checked_parameters(conninfo).get('options') == TIMEOUT_OPTIONS,
                "The subscription source timeout override must remain configured.")
        cursor.execute("""
            SELECT n.nspname, c.relname, sr.srsubstate
            FROM pg_subscription_rel sr JOIN pg_class c ON c.oid = sr.srrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE sr.srsubid = %s
        """, (identifier,))
        relations = cursor.fetchall()
        tracked = {(row[0], row[1]) for row in relations}
        require(tracked in (original, retained) and len(relations) == len(tracked)
                and all(row[2] == 'r' for row in relations),
                "Target subscription must track exactly 24 or 19 tables, all initially synchronized.")
        check_foreign_keys(cursor, retained)
        return identifier, enabled, tracked


def writable_autocommit(connection):
    connection.rollback()
    connection.set_session(isolation_level='READ COMMITTED', readonly=False, autocommit=True)
    with connection.cursor() as cursor:
        cursor.execute("SET search_path = pg_catalog")
        cursor.execute("SET statement_timeout = '30s'")
        cursor.execute("SET lock_timeout = '5s'")


def subscription_command(connection, clause):
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("ALTER SUBSCRIPTION {} ").format(
            sql.Identifier(SUBSCRIPTION)) + sql.SQL(clause))


def wait_for_worker_exit(connection, subscription_id):
    deadline = time.monotonic() + 20
    while True:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pid FROM pg_stat_subscription WHERE subid = %s AND pid IS NOT NULL",
                           (subscription_id,))
            if not cursor.fetchall():
                return
        require(time.monotonic() < deadline,
                "Subscription is disabled, but a worker has not exited; inspect it before retrying --apply.")
        time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Read-only checks (the default).')
    mode.add_argument('--apply', action='store_true', help='Blocked while the reported live OVH cutover is under review.')
    arguments = parser.parse_args()
    source = target = None
    phase = 'configuration'
    mutation_attempted = False
    disabled = False
    try:
        require(not (arguments.apply and OVH_REPORTED_LIVE),
                "OVH was reported as the application database. --apply is blocked because it would "
                "re-enable incoming Supabase replication. Review the live cutover before proceeding.")
        source_parameters, target_parameters, _worker_dsn, original = configuration()
        require(len(original) == 24 and len(EXCLUDED_TABLES) == 5 and EXCLUDED_TABLES <= original,
                "The reviewed 24-table scope and exact five feature exclusions are required.")
        retained = original - EXCLUDED_TABLES
        require(len(retained) == 19, "Exactly 19 application tables must remain in replication.")
        phase = 'source connection'
        source = connect_read_only(source_parameters)
        phase = 'target connection'
        target = connect_read_only(target_parameters)
        phase = 'source preflight'
        source_members = check_source(source, original, retained)
        phase = 'target preflight'
        subscription_id, enabled, target_members = check_target(target, original, retained)
        print('Read-only checks passed: expected databases, existing slot, ready table states and FK boundary.')
        print('Exclude from ongoing replication: ' + ', '.join('.'.join(name) for name in sorted(EXCLUDED_TABLES)))
        print(f'Keep 19 application tables; current publication={len(source_members)}, subscription={len(target_members)}.')
        print('Existing physical tables and copied data are retained.')
        if not arguments.apply:
            print('Read-only check complete. --apply is blocked pending review of the reported live OVH cutover.')
            return 0

        writable_autocommit(source)
        writable_autocommit(target)
        if source_members != retained:
            phase = 'source publication exclusion'
            with source.cursor() as cursor:
                statement = sql.SQL('ALTER PUBLICATION {} DROP TABLE {}').format(
                    sql.Identifier(PUBLICATION),
                    sql.SQL(', ').join(sql.Identifier(*name) for name in sorted(EXCLUDED_TABLES)))
                mutation_attempted = True
                cursor.execute(statement)

        # PostgreSQL 17 rejects REFRESH on a disabled subscription. A previously
        # interrupted run can therefore need ENABLE before its refresh retry.
        mutation_attempted = True
        if not enabled:
            phase = 'subscription enable for refresh'
            subscription_command(target, 'ENABLE')
        phase = 'subscription membership refresh'
        subscription_command(target, 'REFRESH PUBLICATION WITH (copy_data = false)')

        # An existing worker can cache READY relation mappings. Restart it after
        # refresh so removed relations load as UNKNOWN and are ignored, including
        # when a previously failing transaction contains retained-table changes.
        phase = 'subscription disable for worker restart'
        subscription_command(target, 'DISABLE')
        disabled = True
        phase = 'subscription worker shutdown'
        wait_for_worker_exit(target, subscription_id)
        phase = 'source scope verification'
        require(check_source(source, original, retained) == retained,
                'Source scope verification failed; inspect membership before retrying --apply.')
        phase = 'target scope verification'
        actual_id, actual_enabled, actual_members = check_target(target, original, retained)
        require(actual_id == subscription_id and not actual_enabled and actual_members == retained,
                'Target scope verification failed; subscription remains disabled for review.')
        phase = 'verified scope manifest'
        write_applied_scope(original)
        phase = 'subscription restart'
        subscription_command(target, 'ENABLE')
        disabled = False
        phase = 'final subscription verification'
        final_id, final_enabled, final_members = check_target(target, original, retained)
        require(final_id == subscription_id and final_enabled and final_members == retained,
                'Final subscription verification failed; inspect its enabled state before retrying.')
        print('Verified 19-table publication/subscription scope; subscription re-enabled and its worker will start automatically.')
        print('Five feature tables retain their existing data; the original slot and error history were preserved.')
        print('Next: run monitor_replication.py to check apply recovery and WAL catch-up.')
        return 0
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        if mutation_attempted:
            print('Scope changes may have committed. Run --check, then rerun --apply to resume after resolving the guard.',
                  file=sys.stderr)
        if disabled:
            print('The last confirmed subscription state was disabled; check its state before resuming.', file=sys.stderr)
        return 1
    except psycopg2.Error as error:
        # Driver messages and SQL can expose connection strings; omit them.
        print(f'Feature exclusion failed during {phase} ({type(error).__name__}; '
              f'SQLSTATE {error.pgcode or "unavailable"}).', file=sys.stderr)
        if mutation_attempted:
            print('Changes may have committed. Run --check before rerunning --apply to resume.', file=sys.stderr)
        if disabled:
            print('The last confirmed subscription state was disabled; resolve the error before retrying --apply.', file=sys.stderr)
        return 1
    except (SQLAlchemyError, KeyError, TypeError, ValueError, OSError):
        print(f'Feature exclusion configuration/report failed during {phase}; no connection details were printed.',
              file=sys.stderr)
        if mutation_attempted:
            print('Changes may have committed; run --check before retrying --apply.', file=sys.stderr)
        if disabled:
            print('The last confirmed subscription state was disabled; check its state before resuming.', file=sys.stderr)
        return 1
    finally:
        for connection in (source, target):
            if connection is not None:
                connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
