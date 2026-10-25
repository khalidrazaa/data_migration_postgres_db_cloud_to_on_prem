"""Read PostgreSQL 17 replication status without inspecting connection strings or row data."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import sys

import psycopg2
from psycopg2.extras import RealDictCursor
from sqlalchemy.exc import SQLAlchemyError

from prepare_target_schema import PreparationError, WORKSPACE
from migration_scope import effective_tables
from start_replication import (
    PUBLICATION, SLOT, SUBSCRIPTION, configuration, connect_read_only, require,
)


OUTPUT = WORKSPACE / "replication_status.json"
ERROR_COUNTERS = ("apply_error_count", "sync_error_count")
MAX_BASELINE_AGE = timedelta(minutes=15)


def rows(connection, query, parameters=()):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(query, parameters)
        return [dict(row) for row in cursor.fetchall()]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def previous_report():
    try:
        report = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return report if isinstance(report, dict) else None
    except (OSError, TypeError, ValueError):
        return None


def aware_time(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def cumulative_error_counts(statistics):
    if not isinstance(statistics, list) or len(statistics) != 1:
        return None
    row = statistics[0]
    if not isinstance(row, dict):
        return None
    counts = {name: row.get(name) for name in ERROR_COUNTERS}
    if not all(type(value) is int and value >= 0 for value in counts.values()):
        return None
    return counts


def subscription_identity(subscription):
    if not isinstance(subscription, dict):
        return None
    identifier = subscription.get("id")
    publications = subscription.get("publications")
    if (type(identifier) is not int or identifier <= 0
            or not all(isinstance(subscription.get(name), str) and subscription[name]
                       for name in ("name", "slot"))
            or not isinstance(publications, list) or not publications
            or not all(isinstance(name, str) and name for name in publications)
            or len(set(publications)) != len(publications)):
        return None
    return (identifier, subscription["name"], subscription["slot"],
            tuple(sorted(publications)))


def error_delta(previous, subscription, statistics, target_time):
    """Compare target statistics only against a fresh sample of this subscription."""
    if not isinstance(previous, dict):
        return None
    identity = subscription_identity(subscription)
    if identity is None or subscription_identity(previous.get("subscription")) != identity:
        return None
    before = aware_time(previous.get("target_snapshot_utc"))
    now = aware_time(target_time)
    if before is None or now is None or not timedelta(0) < now - before <= MAX_BASELINE_AGE:
        return None
    previous_statistics = previous.get("error_statistics")
    previous_counts = cumulative_error_counts(previous_statistics)
    current_counts = cumulative_error_counts(statistics)
    if previous_counts is None or current_counts is None:
        return None
    reset_markers = []
    for row in (previous_statistics[0], statistics[0]):
        if "stats_reset" not in row:
            return None
        value = row["stats_reset"]
        reset = aware_time(value) if value is not None else None
        if value is not None and reset is None:
            return None
        reset_markers.append(reset)
    if reset_markers[0] != reset_markers[1]:
        return None
    if any(current_counts[name] < previous_counts[name] for name in ERROR_COUNTERS):
        return None
    return {name: current_counts[name] - previous_counts[name] for name in ERROR_COUNTERS}


def main():
    source = target = None
    phase = "configuration"
    try:
        previous = previous_report()
        source_parameters, target_parameters, _worker_dsn, original_tables = configuration()
        tables = effective_tables(original_tables)
        phase = "target connection"
        target = connect_read_only(target_parameters)
        phase = "target status"
        target_time = utc_now()
        require(rows(target, "SELECT current_database() AS database")[0]["database"] == "explainit",
                "The monitor must connect to the explainit target database.")
        subscriptions = rows(target, """
            SELECT oid AS id, subname AS name, subenabled AS enabled,
                   subslotname AS slot, subpublications AS publications
            FROM pg_subscription WHERE subname = %s
              AND subdbid = (SELECT oid FROM pg_database WHERE datname = current_database())
        """, (SUBSCRIPTION,))
        subscription = subscriptions[0] if len(subscriptions) == 1 else None
        subscription_id = subscription["id"] if subscription else 0
        table_states = rows(target, """
            SELECT n.nspname AS schema, c.relname AS table, sr.srsubstate AS state,
                   sr.srsublsn::text AS relation_lsn
            FROM pg_subscription_rel sr JOIN pg_class c ON c.oid = sr.srrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE sr.srsubid = %s ORDER BY n.nspname, c.relname
        """, (subscription_id,))
        workers = rows(target, """
            SELECT s.worker_type, s.pid, s.leader_pid,
                   n.nspname AS schema, c.relname AS table,
                   s.received_lsn::text, s.last_msg_send_time, s.last_msg_receipt_time,
                   s.latest_end_lsn::text, s.latest_end_time
            FROM pg_stat_subscription s LEFT JOIN pg_class c ON c.oid = s.relid
            LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE s.subid = %s ORDER BY s.worker_type, c.relname
        """, (subscription_id,))
        copy_progress = rows(target, """
            SELECT n.nspname AS schema, c.relname AS table, p.pid,
                   p.command, p.type, p.bytes_processed, p.bytes_total,
                   p.tuples_processed
            FROM pg_stat_progress_copy p
            JOIN pg_stat_subscription s ON s.pid = p.pid
            LEFT JOIN pg_class c ON c.oid = p.relid
            LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE s.subid = %s ORDER BY n.nspname, c.relname, p.pid
        """, (subscription_id,))
        errors = rows(target, """
            SELECT apply_error_count, sync_error_count, stats_reset
            FROM pg_stat_subscription_stats WHERE subid = %s
        """, (subscription_id,))
        phase = "source connection"
        source = connect_read_only(source_parameters)
        phase = "source slot status"
        source_time = utc_now()
        require(rows(source, "SELECT current_database() AS database, current_user AS role")[0]
                == {"database": "postgres", "role": "postgres"}, "Unexpected source database or role.")
        publication_tables = rows(source, """
            SELECT schemaname AS schema, tablename AS table
            FROM pg_publication_tables WHERE pubname = %s ORDER BY schemaname, tablename
        """, (PUBLICATION,))
        publication_matches_scope = (
            len(publication_tables) == len(tables)
            and {(row["schema"], row["table"]) for row in publication_tables} == tables)
        slots = rows(source, """
            SELECT slot_name, plugin, slot_type, database, active, active_pid,
                   restart_lsn::text, confirmed_flush_lsn::text, wal_status,
                   safe_wal_size, invalidation_reason,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint AS confirmed_lag_bytes,
                   pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint AS retained_wal_bytes
            FROM pg_replication_slots WHERE slot_name = %s
        """, (SLOT,))
        slot = slots[0] if len(slots) == 1 else None
        states = dict(sorted(Counter(row["state"] for row in table_states).items()))
        actual_tables = {(row["schema"], row["table"]) for row in table_states}
        complete = (len(table_states) == len(tables) and actual_tables == tables
                    and all(row["state"] == "r" for row in table_states))
        apply_workers = [row for row in workers if row["worker_type"] == "apply" and row["pid"]]
        sync_workers = [row for row in workers if row["worker_type"] == "table synchronization" and row["pid"]]
        cumulative_errors = cumulative_error_counts(errors)
        known_zero_errors = cumulative_errors is not None and not any(cumulative_errors.values())
        error_history_present = (any(cumulative_errors.values())
                                 if cumulative_errors is not None else None)
        new_errors = error_delta(previous, subscription, errors, target_time)
        no_new_errors = new_errors is not None and not any(new_errors.values())
        subscription_ok = bool(subscription and subscription["enabled"]
                               and subscription["slot"] == SLOT
                               and subscription["publications"] == [PUBLICATION])
        slot_ok = bool(slot and slot["slot_name"] == SLOT and slot["active"]
                       and slot["plugin"] == "pgoutput" and slot["slot_type"] == "logical"
                       and slot["database"] == "postgres" and slot["wal_status"] in ("reserved", "extended")
                       and slot["invalidation_reason"] is None)
        running = bool(subscription_ok and apply_workers and slot_ok and publication_matches_scope)
        healthy = running and (known_zero_errors or no_new_errors)
        ready = complete and healthy
        report = {
            "collected_at_utc": utc_now(), "target_snapshot_utc": target_time,
            "source_snapshot_utc": source_time, "subscription": subscription,
            "expected_tables": [".".join(name) for name in sorted(tables)],
            "excluded_tables": [".".join(name) for name in sorted(original_tables - tables)],
            "publication_tables": publication_tables,
            "publication_matches_scope": publication_matches_scope,
            "table_state_counts": states, "table_states": table_states,
            "not_ready_tables": [row for row in table_states if row["state"] != "r"],
            "missing_tables": [".".join(name) for name in sorted(tables - actual_tables)],
            "unexpected_tables": [".".join(name) for name in sorted(actual_tables - tables)],
            "workers": workers, "copy_progress": copy_progress,
            "error_statistics": errors, "errors_known_zero": known_zero_errors,
            "cumulative_errors": cumulative_errors, "error_history_present": error_history_present,
            "new_errors_since_previous_sample": new_errors,
            "source_slot": slot, "initial_copy_complete": complete,
            "replication_running": running, "replication_healthy": healthy,
            "ready_for_validation": ready,
            "note": "Independent read-only snapshots. Status does not prove data equality or complete cutover.",
        }
        OUTPUT.write_text(json.dumps(report, indent=2, default=lambda value:
            value.astimezone(timezone.utc).isoformat()) + "\n", encoding="utf-8")
        print(f"Initial copy: {states.get('r', 0)}/{len(tables)} tables ready; states={states}; complete={complete}.")
        print(f"Publication matches scope={publication_matches_scope}; excluded tables={len(original_tables - tables)}.")
        error_summary = (f"apply={cumulative_errors['apply_error_count']}, "
                         f"sync={cumulative_errors['sync_error_count']}" if cumulative_errors is not None
                         else "unknown (statistics unavailable or invalid)")
        new_error_summary = (f"apply={new_errors['apply_error_count']}, sync={new_errors['sync_error_count']}"
                             if new_errors is not None else "unknown (no comparable sample within 15 minutes)")
        print(f"Replication running={running}; healthy={healthy}; "
              f"live apply={len(apply_workers)}, sync={len(sync_workers)}.")
        for progress in copy_progress:
            processed_mb = progress["bytes_processed"] / 1_000_000
            total = (f"{progress['bytes_total'] / 1_000_000:.1f} MB total"
                     if progress["bytes_total"] else "total unknown")
            print(f"COPY {progress['schema']}.{progress['table']}: "
                  f"{processed_mb:.1f} MB, {progress['tuples_processed']} rows processed; {total}.")
        print(f"Cumulative errors: {error_summary}; history_present={error_history_present}.")
        print(f"New errors since previous target sample: {new_error_summary}.")
        print("Source slot: " + (f"active={slot['active']}, plugin={slot['plugin']}, "
              f"wal_status={slot['wal_status']}, confirmed_lag_bytes={slot['confirmed_lag_bytes']}, "
              f"retained_wal_bytes={slot['retained_wal_bytes']}" if slot else "missing"))
        print(f"Ready for data validation={ready}. Report: {OUTPUT.name}.")
        print("This status does not establish data equality or complete cutover.")
        return 0 if healthy else 2
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except psycopg2.Error as error:
        print(f"Monitor failed during {phase} ({type(error).__name__}; "
              f"SQLSTATE {error.pgcode or 'unavailable'}).", file=sys.stderr)
        return 1
    except (SQLAlchemyError, KeyError, TypeError, ValueError, OSError):
        print("Monitor configuration/report failed; check .env and the reviewed schema report.", file=sys.stderr)
        return 1
    finally:
        for connection in (source, target):
            if connection is not None:
                connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
