"""Audit the live OVH database using read-only snapshots and aggregate results.

The two snapshots are independent. OVH has accepted application writes, so row
differences are observations to review, not automatic evidence of corruption.
No row contents, primary keys, hashes or connection strings enter the report.
No data, subscription or sequence state is changed.
"""

import argparse
from datetime import datetime, timezone
import json
import re
import sys

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from sqlalchemy.exc import SQLAlchemyError

from migration_scope import effective_tables
from prepare_target_schema import PreparationError, WORKSPACE
from start_replication import (
    PUBLICATION, SLOT, SUBSCRIPTION, configuration, connect_read_only,
    require, revision,
)


OUTPUT = WORKSPACE / "live_target_audit.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def rows(connection, query, parameters=None):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(query, parameters)
        return [dict(row) for row in cursor.fetchall()]


def prepare_snapshot(connection, label):
    identity = rows(connection, """
        SELECT current_database() AS database, current_user AS role,
               current_setting('server_version_num')::int AS version,
               current_setting('transaction_read_only') AS read_only,
               current_setting('transaction_isolation') AS isolation,
               current_setting('server_encoding') AS encoding,
               transaction_timestamp() AS transaction_started_at
    """)[0]
    expected = ('postgres', 'postgres') if label == 'source' else ('explainit', 'explainit_admin')
    require((identity['database'], identity['role']) == expected
            and 170000 <= identity['version'] < 180000
            and identity['read_only'] == 'on' and identity['isolation'] == 'repeatable read'
            and identity['encoding'] == 'UTF8',
            f"Unexpected {label} database or read-only snapshot configuration.")
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL TimeZone = 'UTC'")
        cursor.execute("SET LOCAL DateStyle = 'ISO, YMD'")
        cursor.execute("SET LOCAL IntervalStyle = 'postgres'")
        cursor.execute("SET LOCAL extra_float_digits = 3")
        revision(cursor, label)
    identity['transaction_started_at'] = identity['transaction_started_at'].astimezone(timezone.utc).isoformat()
    return identity


def target_guard(connection, tables):
    subscriptions = rows(connection, """
        SELECT oid AS id, subname AS name, subenabled AS enabled,
               subslotname AS slot, subpublications AS publications
        FROM pg_subscription WHERE subdbid =
            (SELECT oid FROM pg_database WHERE datname = current_database())
    """)
    require(len(subscriptions) == 1, "Expected only the preserved migration subscription on OVH.")
    subscription = subscriptions[0]
    require(subscription['name'] == SUBSCRIPTION and not subscription['enabled']
            and subscription['slot'] == SLOT and subscription['publications'] == [PUBLICATION],
            "Incoming replication must remain disabled on the expected OVH subscription.")
    relations = rows(connection, """
        SELECT n.nspname AS schema, c.relname AS table, sr.srsubstate AS state
        FROM pg_subscription_rel sr JOIN pg_class c ON c.oid = sr.srrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE sr.srsubid = %s
    """, (subscription['id'],))
    require(len(relations) == len(tables)
            and {(row['schema'], row['table']) for row in relations} == tables
            and all(row['state'] == 'r' for row in relations),
            "The subscription must retain exactly 19 initially synchronized table states.")
    workers = rows(connection, """
        SELECT pid FROM pg_stat_subscription WHERE subid = %s AND pid IS NOT NULL
    """, (subscription['id'],))
    require(not workers, "Replication workers must exit before auditing the live target.")
    return {'subscription_id': subscription['id'], 'enabled': False,
            'live_workers': 0, 'ready_tables': len(relations), 'checked_at_utc': utc_now()}


def table_shape(connection, table):
    schema, name = table
    columns = rows(connection, """
        SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p')
          AND a.attnum > 0 AND NOT a.attisdropped
    """, (schema, name))
    primary_key = rows(connection, """
        SELECT a.attname AS name FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, position) ON true
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
        WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
          AND i.indisvalid AND i.indisready AND i.indislive
          AND k.position <= i.indnkeyatts ORDER BY k.position
    """, (schema, name))
    require(columns and primary_key, "Every retained table needs columns and a valid primary key.")
    return ({row['name']: row['type'] for row in columns}, [row['name'] for row in primary_key])


def digest_stream(connection, table, primary_key, cursor_name):
    key = sql.SQL('jsonb_build_array({})::text').format(
        sql.SQL(', ').join(sql.Identifier('t', name) for name in primary_key))
    statement = sql.SQL("""
        SELECT {key}, encode(sha256(convert_to(to_jsonb(t)::text, 'UTF8')), 'hex')
        FROM {table} AS t ORDER BY ({key}) COLLATE "C"
    """).format(key=key, table=sql.Identifier(*table))
    with connection.cursor(name=cursor_name) as cursor:
        cursor.itersize = 1000
        cursor.execute(statement)
        for primary_key_text, digest in cursor:
            yield primary_key_text.encode('utf-8'), digest


def compare_streams(source_stream, target_stream):
    source_iterator, target_iterator = iter(source_stream), iter(target_stream)
    source, target = next(source_iterator, None), next(target_iterator, None)
    result = {'source_count': 0, 'target_count': 0, 'identical': 0,
              'changed': 0, 'source_only': 0, 'target_only': 0}
    while source is not None or target is not None:
        if target is None or (source is not None and source[0] < target[0]):
            result['source_count'] += 1
            result['source_only'] += 1
            source = next(source_iterator, None)
        elif source is None or target[0] < source[0]:
            result['target_count'] += 1
            result['target_only'] += 1
            target = next(target_iterator, None)
        else:
            result['source_count'] += 1
            result['target_count'] += 1
            result['identical' if source[1] == target[1] else 'changed'] += 1
            source, target = next(source_iterator, None), next(target_iterator, None)
    return result


def sequence_assessment(maximum_id, last_value, is_called, increment, minimum, maximum, cache, cycle):
    candidate = last_value + increment if is_called else last_value
    supported = increment == 1 and cache == 1 and not cycle
    exhausted = not minimum <= candidate <= maximum
    occupied_range = maximum_id is not None and candidate <= maximum_id if supported else None
    return {'hypothetical_next': candidate, 'parameters_supported': supported,
            'outside_sequence_bounds': exhausted,
            'potential_occupied_range': occupied_range,
            'needs_review': not supported or exhausted or bool(occupied_range)}


def audit_sequences(connection, tables):
    sequences = rows(connection, """
        SELECT s.seqrelid AS oid, n.nspname AS schema, c.relname AS sequence,
               tn.nspname AS owner_schema, tc.relname AS owner_table, a.attname AS owner_column,
               format_type(a.atttypid, NULL) AS owner_type, a.attidentity AS identity,
               pg_get_expr(ad.adbin, ad.adrelid, false) AS column_default,
               s.seqincrement AS increment, s.seqmin AS minimum, s.seqmax AS maximum,
               s.seqcache AS cache, s.seqcycle AS cycle,
               EXISTS (SELECT 1 FROM pg_depend dd
                       WHERE dd.classid = 'pg_attrdef'::regclass AND dd.objid = ad.oid
                         AND dd.refclassid = 'pg_class'::regclass AND dd.refobjid = s.seqrelid)
                   AS default_depends_on_sequence
        FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_depend d ON d.classid = 'pg_class'::regclass AND d.objid = c.oid
             AND d.objsubid = 0 AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
        JOIN pg_class tc ON tc.oid = d.refobjid
        JOIN pg_namespace tn ON tn.oid = tc.relnamespace
        JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = d.refobjsubid
        LEFT JOIN pg_attrdef ad ON ad.adrelid = tc.oid AND ad.adnum = a.attnum
        WHERE n.nspname = 'public' ORDER BY c.relname
    """)
    selected = [row for row in sequences if (row['owner_schema'], row['owner_table']) in tables]
    require(len(selected) == 13 and len({row['oid'] for row in selected}) == 13,
            "Expected exactly 13 sequences owned by the retained tables.")
    result = []
    for sequence in selected:
        table = (sequence['owner_schema'], sequence['owner_table'])
        qualified_table = sql.Identifier(*table).as_string(connection)
        bound = rows(connection, 'SELECT pg_get_serial_sequence(%s, %s)::regclass::oid AS oid',
                     (qualified_table, sequence['owner_column']))[0]['oid']
        require(bound == sequence['oid'], "Sequence ownership lookup changed during the audit.")
        simple_default = bool(sequence['identity']) or (
            sequence['default_depends_on_sequence']
            and re.fullmatch(r"nextval\('(?:[^']|'')+'::regclass\)", sequence['column_default'] or '') is not None)
        require(sequence['owner_type'] in ('smallint', 'integer', 'bigint') and simple_default,
                "A retained sequence has an unexpected owner type or column default; review it first.")
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('SELECT max({}) FROM {}').format(
                sql.Identifier(sequence['owner_column']), sql.Identifier(*table)))
            maximum_id = cursor.fetchone()[0]
            cursor.execute(sql.SQL('SELECT last_value, is_called FROM {}').format(
                sql.Identifier(sequence['schema'], sequence['sequence'])))
            last_value, is_called = cursor.fetchone()
        assessment = sequence_assessment(maximum_id, last_value, is_called,
                                         sequence['increment'], sequence['minimum'], sequence['maximum'],
                                         sequence['cache'], sequence['cycle'])
        result.append({
            'sequence': '.'.join((sequence['schema'], sequence['sequence'])),
            'owner_table': '.'.join(table), 'owner_column': sequence['owner_column'],
            'table_snapshot_maximum': maximum_id, 'observed_last_value': last_value,
            'observed_is_called': is_called, 'increment': sequence['increment'],
            'cache': sequence['cache'], 'cycle': sequence['cycle'],
            'observed_at_utc': utc_now(), **assessment,
        })
    return result


def save_report(report):
    report['finished_at_utc'] = utc_now()
    OUTPUT.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    source = target = None
    phase = 'configuration'
    report = {
        'started_at_utc': utc_now(), 'status': 'in_progress', 'tables': [], 'sequences': [],
        'notes': [
            'Source writers are reported stopped; OVH has accepted application writes since approximately 2026-10-24.',
            'Snapshots are independent and OVH remains live. Missing, changed and target-only rows need review; no automatic data repair is implied.',
            'SHA-256 compares canonical JSONB values, not byte-identical JSON formatting or duplicate JSON keys.',
            'Only aggregate row results are retained. No row contents, primary keys or hashes are saved.',
            'Sequence state is not an MVCC snapshot and may advance while the table snapshot is held.',
            'Hypothetical sequence values below an occupied range indicate potential collisions, not certainty that the next insert fails.',
            'No data writes, sequence advances, subscription changes or orphan checks are performed.',
        ],
    }
    try:
        source_parameters, target_parameters, _worker_dsn, original_tables = configuration()
        tables = effective_tables(original_tables)
        require(len(tables) == 19, 'The verified 19-table retained scope is required for this live audit.')
        report['retained_tables'] = ['.'.join(table) for table in sorted(tables)]
        phase = 'target connection'
        target = connect_read_only(target_parameters)
        phase = 'target snapshot guards'
        report['target_snapshot'] = prepare_snapshot(target, 'target')
        report['subscription_at_start'] = target_guard(target, tables)
        phase = 'source connection'
        source = connect_read_only(source_parameters)
        phase = 'source snapshot guards'
        report['source_snapshot'] = prepare_snapshot(source, 'source')
        for number, table in enumerate(sorted(tables)):
            name = '.'.join(table)
            phase = 'column and primary-key checks for ' + name
            source_shape, target_shape = table_shape(source, table), table_shape(target, table)
            require(source_shape == target_shape,
                    'Source and target column names/types or primary keys differ; review schema before comparing values.')
            phase = 'canonical row comparison for ' + name
            counts = compare_streams(
                digest_stream(source, table, source_shape[1], 'audit_source_' + str(number)),
                digest_stream(target, table, target_shape[1], 'audit_target_' + str(number)))
            report['tables'].append({'table': name, **counts})
        phase = 'target sequence observations'
        report['sequences'] = audit_sequences(target, tables)
        phase = 'final disabled-subscription guard'
        target.rollback()
        prepare_snapshot(target, 'target')
        report['subscription_at_end'] = target_guard(target, tables)
        report['status'] = 'completed_read_only'
        report['summary'] = {
            'tables_compared': len(report['tables']),
            'tables_with_differences': sum(any(row[key] for key in ('changed', 'source_only', 'target_only'))
                                          for row in report['tables']),
            'sequences_observed': len(report['sequences']),
            'sequences_requiring_review': sum(row['needs_review'] for row in report['sequences']),
        }
        phase = 'report output'
        save_report(report)
        summary = report['summary']
        print(f"Read-only comparison completed for {summary['tables_compared']} retained tables; "
              f"{summary['tables_with_differences']} have observed differences.")
        for row in report['tables']:
            if any(row[key] for key in ('changed', 'source_only', 'target_only')):
                print(f"{row['table']}: source={row['source_count']}, OVH={row['target_count']}, "
                      f"identical={row['identical']}, changed={row['changed']}, "
                      f"source_only={row['source_only']}, OVH_only={row['target_only']}.")
        print(f"Observed {summary['sequences_observed']} sequences; "
              f"{summary['sequences_requiring_review']} require review.")
        for sequence in report['sequences']:
            if sequence['needs_review']:
                print(f"{sequence['sequence']}: hypothetical_next={sequence['hypothetical_next']}, "
                      f"table_snapshot_maximum={sequence['table_snapshot_maximum']}, "
                      f"parameters_supported={sequence['parameters_supported']}.")
        print('Incoming replication remains disabled with zero live workers. No database or sequence state was changed.')
        print(f'Aggregate report: {OUTPUT.name}. Differences require interpretation against legitimate OVH writes.')
        return 0
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        report['failure'] = {'phase': phase, 'type': type(error).__name__}
    except psycopg2.Error as error:
        # Driver text, detail, context and SQL may expose row data or credentials.
        print(f'Audit failed during {phase} ({type(error).__name__}; '
              f'SQLSTATE {error.pgcode or "unavailable"}).', file=sys.stderr)
        report['failure'] = {'phase': phase, 'type': type(error).__name__, 'sqlstate': error.pgcode}
    except (SQLAlchemyError, KeyError, TypeError, ValueError, OSError) as error:
        print(f'Audit configuration/report failed during {phase}; no row or connection details were printed.', file=sys.stderr)
        report['failure'] = {'phase': phase, 'type': type(error).__name__}
    finally:
        for connection in (source, target):
            if connection is not None:
                connection.close()
    report['status'] = 'failed'
    try:
        save_report(report)
    except (OSError, TypeError, ValueError):
        print('The incomplete audit report could not be saved.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
