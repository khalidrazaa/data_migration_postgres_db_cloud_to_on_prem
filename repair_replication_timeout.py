"""Check the subscription timeout; --apply changes only its source session options.

The default and --check are read-only. Connection strings and driver messages are
never printed. No publication, subscription, slot or table is recreated or reset.
"""

import argparse
import sys

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
from sqlalchemy.exc import SQLAlchemyError

from prepare_target_schema import PreparationError, load_target
from start_replication import (
    DIRECT_HOST, PUBLICATION, SLOT, SUBSCRIPTION,
    connection_parameters, connect_read_only, require,
)


TIMEOUT_OPTIONS = "-c statement_timeout=0"


def subscription_conninfo(connection):
    """Return credentials only in memory, after checking the intended target/subscription."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user")
        require(cursor.fetchone() == ("explainit", "explainit_admin"),
                "Expected the explainit target as explainit_admin.")
        cursor.execute("""
            SELECT subenabled, subslotname, subpublications, subconninfo
            FROM pg_subscription WHERE subname = %s AND subdbid =
                (SELECT oid FROM pg_database WHERE datname = current_database())
        """, (SUBSCRIPTION,))
        records = cursor.fetchall()
        require(len(records) == 1, "The expected target subscription must exist exactly once.")
        enabled, slot, publications, conninfo = records[0]
        require(enabled and slot == SLOT and publications == [PUBLICATION],
                "The enabled subscription must retain its expected slot and publication.")
        return conninfo


def checked_parameters(conninfo):
    # parse_dsn failures can contain the input, so callers must never print them.
    parameters = parse_dsn(conninfo)
    require(parameters.get("host") == DIRECT_HOST
            and parameters.get("port", "5432") == "5432"
            and parameters.get("dbname") == "postgres"
            and parameters.get("user") == "postgres"
            and parameters.get("sslmode") == "require"
            and bool(parameters.get("password"))
            and not parameters.get("hostaddr"),
            "Subscription connection must use the confirmed direct source endpoint, role and required SSL.")
    require(parameters.get("options", "") in ("", TIMEOUT_OPTIONS),
            "Existing connection options are not recognized; no change was made.")
    return parameters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Run read-only checks (the default).")
    mode.add_argument("--apply", action="store_true", help="Set statement_timeout=0 for subscription source sessions.")
    arguments = parser.parse_args()
    connection = None
    phase = "configuration"
    alter_attempted = False
    try:
        parameters = connection_parameters(load_target())
        phase = "target connection"
        connection = connect_read_only(parameters)
        phase = "subscription inspection"
        original = checked_parameters(subscription_conninfo(connection))
        if original.get("options") == TIMEOUT_OPTIONS:
            print("Subscription source statement_timeout=0 is already configured; no change made.")
            return 0
        print("Read-only check passed: expected target, subscription, direct source endpoint and SSL.")
        if not arguments.apply:
            print("The subscription source timeout override is absent. Use --apply to set it for replication sessions only.")
            return 0
        updated = dict(original, options=TIMEOUT_OPTIONS)
        new_conninfo = make_dsn(**updated)
        connection.rollback()
        connection.set_session(isolation_level="READ COMMITTED", readonly=False, autocommit=True)
        phase = "subscription timeout update"
        with connection.cursor() as cursor:
            statement = sql.SQL("ALTER SUBSCRIPTION {} CONNECTION {}").format(
                sql.Identifier(SUBSCRIPTION), sql.Literal(new_conninfo))
            alter_attempted = True
            cursor.execute(statement)
        phase = "subscription timeout verification"
        actual = checked_parameters(subscription_conninfo(connection))
        require(actual == updated, "Subscription update verification failed; inspect its configuration before retrying.")
        print("Subscription source statement_timeout=0 configured and verified; all other connection fields preserved.")
        print("Workers reconnect automatically. A COPY already running with the old timeout may fail once before retrying.")
        print("Next: monitor the remaining table copy and new error-counter increments.")
        return 0
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except psycopg2.Error as error:
        print(f"Timeout repair failed during {phase} ({type(error).__name__}; "
              f"SQLSTATE {error.pgcode or 'unavailable'}).", file=sys.stderr)
        if alter_attempted:
            print("The update may have committed; run the read-only check before retrying.", file=sys.stderr)
        return 1
    except (SQLAlchemyError, KeyError, TypeError, ValueError, OSError):
        print("Timeout repair configuration or verification failed; no connection details were printed.", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
