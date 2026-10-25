"""Plan or transactionally repair retained ID sequences on live OVH.

Default/--check is read-only. --apply briefly locks all 13 owner tables and
their sequences, recomputes positions, and only restarts sequences that are
behind the occupied ID range. It never connects to the source database.
"""

import argparse
from datetime import datetime, timezone
import json
import sys

import psycopg2
from psycopg2 import sql
from sqlalchemy.exc import SQLAlchemyError

from live_target_audit import audit_sequences, prepare_snapshot, rows, target_guard
from migration_scope import effective_tables
from prepare_target_schema import PreparationError, WORKSPACE
from start_replication import configuration, connect_read_only, require, revision


OUTPUT = WORKSPACE / 'target_sequence_repair_plan.json'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def event_trigger_guard(connection):
    enabled = rows(connection, "SELECT oid FROM pg_event_trigger WHERE evtenabled <> 'D'")
    require(not enabled,
            'Enabled event triggers require review before sequence metadata commands can be used.')


def sequence_catalog(connection, tables):
    records = rows(connection, """
        SELECT s.seqrelid AS sequence_oid, sn.nspname AS sequence_schema,
               sc.relname AS sequence_name, sc.relowner AS owner_oid,
               r.rolname AS owner_role, tn.nspname AS table_schema,
               tc.relname AS table_name, tc.oid AS table_oid, tc.relkind AS table_kind,
               a.attname AS column_name, a.attnum AS column_number,
               a.attidentity AS identity, d.deptype AS ownership_kind,
               pg_get_expr(ad.adbin, ad.adrelid, false) AS column_default,
               s.seqincrement AS increment, s.seqmin AS minimum, s.seqmax AS maximum,
               s.seqcache AS cache, s.seqcycle AS cycle
        FROM pg_sequence s JOIN pg_class sc ON sc.oid = s.seqrelid
        JOIN pg_namespace sn ON sn.oid = sc.relnamespace
        JOIN pg_roles r ON r.oid = sc.relowner
        JOIN pg_depend d ON d.classid = 'pg_class'::regclass AND d.objid = s.seqrelid
             AND d.objsubid = 0 AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
        JOIN pg_class tc ON tc.oid = d.refobjid
        JOIN pg_namespace tn ON tn.oid = tc.relnamespace
        JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = d.refobjsubid
        LEFT JOIN pg_attrdef ad ON ad.adrelid = tc.oid AND ad.adnum = a.attnum
        WHERE sn.nspname = 'public'
        ORDER BY sn.nspname, sc.relname
    """)
    selected = [record for record in records
                if (record['table_schema'], record['table_name']) in tables]
    require(len(selected) == 13 and len({record['sequence_oid'] for record in selected}) == 13,
            'Exactly 13 retained owned sequences are required.')
    require(all(record['owner_role'] == 'explainit_admin' and record['table_kind'] == 'r'
                and record['increment'] == 1 and record['cache'] == 1 and not record['cycle']
                for record in selected),
            'Sequence owner, table kind or sequence parameters require review before any repair.')
    return {record['sequence_schema'] + '.' + record['sequence_name']: record for record in selected}


def proposed_repairs(observations, catalog):
    require(len(observations) == 13 and len({item['sequence'] for item in observations}) == 13
            and {item['sequence'] for item in observations} == set(catalog),
            'Sequence observations do not match the 13 verified owned sequences.')
    repairs = []
    for observation in observations:
        metadata = catalog[observation['sequence']]
        require(observation['parameters_supported'] and not observation['outside_sequence_bounds']
                and observation['increment'] == metadata['increment']
                and observation['cache'] == metadata['cache']
                and observation['cycle'] == metadata['cycle'],
                'A sequence has unsupported parameters or is exhausted; no automatic repair is permitted.')
        if not observation['potential_occupied_range']:
            require(not observation['needs_review'], 'A sequence requires review outside the supported repair.')
            continue
        maximum_id = observation['table_snapshot_maximum']
        require(type(maximum_id) is int, 'A behind sequence has no usable integer maximum ID.')
        restart = max(observation['hypothetical_next'], maximum_id + 1)
        require(metadata['minimum'] <= restart <= metadata['maximum'],
                'An occupied ID range reaches the configured sequence bounds; review it manually.')
        require(restart >= observation['hypothetical_next'], 'Sequence repair must never move backward.')
        repairs.append({
            'sequence': observation['sequence'],
            'owner_table': observation['owner_table'],
            'owner_column': observation['owner_column'],
            'observed_hypothetical_next': observation['hypothetical_next'],
            'table_snapshot_maximum': maximum_id,
            'proposed_restart': restart,
        })
    return sorted(repairs, key=lambda item: item['sequence'])


def write_transaction_setup(connection):
    connection.rollback()
    connection.set_session(isolation_level='READ COMMITTED', readonly=False, autocommit=False)
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL search_path = pg_catalog")
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '15s'")
        cursor.execute("SET LOCAL transaction_timeout = '45s'")
        cursor.execute("""SELECT current_database(), current_user,
            current_setting('server_version_num')::int,
            current_setting('transaction_read_only'), current_setting('transaction_isolation')""")
        database, user, version, read_only, isolation = cursor.fetchone()
        require((database, user) == ('explainit', 'explainit_admin')
                and 170000 <= version < 180000 and read_only == 'off' and isolation == 'read committed',
                'Unexpected target or transaction configuration; no sequence changes are permitted.')
        revision(cursor, 'Target')


def lock_tables_and_sequences(connection, tables, expected_catalog):
    owner_tables = sorted({(item['table_schema'], item['table_name'])
                           for item in expected_catalog.values()})
    require(len(owner_tables) == 13, 'Expected one retained owner table per sequence.')
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(
            sql.SQL(', ').join(sql.Identifier(*table) for table in owner_tables)))
    require(sequence_catalog(connection, tables) == expected_catalog,
            'Sequence ownership, bindings or configuration changed before sequence locking.')
    event_trigger_guard(connection)
    # The verified same-owner operation obtains the sequence relation lock but
    # does not change ownership. Event hooks are checked because this is DDL.
    with connection.cursor() as cursor:
        for name in sorted(expected_catalog):
            item = expected_catalog[name]
            cursor.execute(sql.SQL('ALTER SEQUENCE {} OWNER TO {}').format(
                sql.Identifier(item['sequence_schema'], item['sequence_name']),
                sql.Identifier(item['owner_role'])))
    require(sequence_catalog(connection, tables) == expected_catalog,
            'Sequence ownership, bindings or configuration changed during sequence locking.')


def save_report(report):
    report['finished_at_utc'] = utc_now()
    OUTPUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def print_plan(repairs):
    print(f'Read-only preflight: 13 retained owned sequences checked; {len(repairs)} are behind the occupied ID range.')
    for repair in repairs:
        print(f"{repair['sequence']}: hypothetical_next={repair['observed_hypothetical_next']} "
              f"-> proposed_restart={repair['proposed_restart']} "
              f"(table maximum={repair['table_snapshot_maximum']}).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Read-only checks and a repair plan (the default).')
    mode.add_argument('--apply', action='store_true', help='Lock, recompute, and transactionally repair only behind sequences.')
    arguments = parser.parse_args()
    connection = None
    phase = 'configuration'
    commit_attempted = committed = False
    report = {
        'started_at_utc': utc_now(), 'status': 'in_progress', 'requested_apply': arguments.apply,
        'notes': [
            'This helper connects only to OVH; no source connection is made.',
            'Read-only positions are observations; --apply recomputes all values after obtaining locks.',
            'Apply briefly blocks owner-table writers and sequence allocation. Ordinary reads continue.',
            'Same-owner metadata DDL is used to acquire sequence locks after ownership/event-trigger guards.',
            'Only transactional RESTART commands adjust positions; failed pre-commit transactions are rolled back.',
            'Post-commit observations can legitimately advance as OVH writers resume.',
        ],
    }
    try:
        _source_parameters, target_parameters, _worker_dsn, original_tables = configuration()
        tables = effective_tables(original_tables)
        require(len(tables) == 19, 'The verified 19-table scope is required.')
        phase = 'target connection'
        connection = connect_read_only(target_parameters)
        phase = 'read-only target guards'
        report['preflight_snapshot'] = prepare_snapshot(connection, 'target')
        report['subscription_at_start'] = target_guard(connection, tables)
        event_trigger_guard(connection)
        phase = 'read-only sequence observations'
        catalog = sequence_catalog(connection, tables)
        observations = audit_sequences(connection, tables)
        repairs = proposed_repairs(observations, catalog)
        report['preflight_observations'] = observations
        report['proposed_repairs'] = repairs
        print_plan(repairs)
        if not arguments.apply:
            report['status'] = 'read_only_plan'
            save_report(report)
            print(f'Plan saved: {OUTPUT.name}. No database or sequence state was changed.')
            return 0
        if not repairs:
            report['status'] = 'no_action_needed'
            save_report(report)
            print('No corrections needed; no table locks or sequence metadata commands were issued.')
            return 0

        print('Applying one bounded transaction; owner-table writes and sequence allocation may briefly wait.')
        phase = 'write transaction guards'
        write_transaction_setup(connection)
        target_guard(connection, tables)
        event_trigger_guard(connection)
        phase = 'table and sequence locking'
        lock_tables_and_sequences(connection, tables, catalog)
        phase = 'locked sequence observations'
        locked_catalog = sequence_catalog(connection, tables)
        locked_observations = audit_sequences(connection, tables)
        locked_repairs = proposed_repairs(locked_observations, locked_catalog)
        report['locked_observations'] = locked_observations
        report['applied_repairs'] = locked_repairs
        phase = 'transactional sequence restarts'
        with connection.cursor() as cursor:
            for repair in locked_repairs:
                item = locked_catalog[repair['sequence']]
                cursor.execute(sql.SQL('ALTER SEQUENCE {} RESTART WITH {}').format(
                    sql.Identifier(item['sequence_schema'], item['sequence_name']),
                    sql.Literal(repair['proposed_restart'])))
        phase = 'locked repair verification'
        verified = audit_sequences(connection, tables)
        require(not proposed_repairs(verified, locked_catalog)
                and not any(item['needs_review'] for item in verified),
                'Locked sequence verification failed; the transaction will be rolled back.')
        target_guard(connection, tables)
        report['locked_verification'] = verified
        phase = 'transaction commit'
        commit_attempted = True
        connection.commit()
        committed = True
        report['transaction_committed'] = True

        phase = 'post-commit read-only verification'
        connection.set_session(isolation_level='REPEATABLE READ', readonly=True, autocommit=False)
        report['post_commit_snapshot'] = prepare_snapshot(connection, 'target')
        report['subscription_at_end'] = target_guard(connection, tables)
        require(sequence_catalog(connection, tables) == catalog,
                'Sequence configuration changed after commit; inspect current metadata before any retry.')
        final_observations = audit_sequences(connection, tables)
        report['post_commit_observations'] = final_observations
        remaining = proposed_repairs(final_observations, catalog)
        report['remaining_repairs'] = remaining
        report['status'] = 'applied_and_verified' if not remaining else 'applied_follow_up_review'
        phase = 'report output'
        save_report(report)
        print(f'Transaction committed: {len(locked_repairs)} sequences restarted without moving backward.')
        print(f'Fresh read-only check: 13 sequences observed; {len(remaining)} remain behind the occupied range.')
        print('Incoming replication remains disabled with zero live workers. No application rows or source data were changed.')
        print(f'Report: {OUTPUT.name}.')
        return 0 if not remaining else 2
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        report['failure'] = {'phase': phase, 'type': type(error).__name__}
    except psycopg2.Error as error:
        print(f'Sequence repair failed during {phase} ({type(error).__name__}; '
              f'SQLSTATE {error.pgcode or "unavailable"}).', file=sys.stderr)
        report['failure'] = {'phase': phase, 'type': type(error).__name__, 'sqlstate': error.pgcode}
    except (SQLAlchemyError, KeyError, TypeError, ValueError, OSError) as error:
        print(f'Sequence repair configuration/report failed during {phase}; no connection details were printed.',
              file=sys.stderr)
        report['failure'] = {'phase': phase, 'type': type(error).__name__}
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except psycopg2.Error:
                pass
            connection.close()
    report['transaction_committed'] = committed
    report['commit_result_may_be_unknown'] = commit_attempted and not committed
    report['status'] = 'failed_after_commit' if committed else ('commit_result_unknown' if commit_attempted else 'failed_rolled_back')
    if committed:
        print('The transaction committed. Run --check to inspect current positions before any further action.', file=sys.stderr)
    elif commit_attempted:
        print('Commit acknowledgement was not confirmed. Run --check before retrying; changes may have committed.', file=sys.stderr)
    else:
        print('No repair transaction committed; any pending sequence restarts were rolled back.', file=sys.stderr)
    try:
        save_report(report)
    except (OSError, TypeError, ValueError):
        print('The sequence repair report could not be saved.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
