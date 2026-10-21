"""Build the empty OVH target using migrations from a separate backend checkout."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError


WORKSPACE = Path(__file__).resolve().parent
TARGET_DATABASE = "explainit_icu"
REVISION = "c03_youtube_scan_controls"


class PreparationError(RuntimeError):
    """A configuration or target guard failed without exposing credentials."""


def load_target():
    settings = dotenv_values(WORKSPACE / ".env", interpolate=False)
    target = make_url(settings["TARGET_DATABASE_URL"])
    source = make_url(settings["SOURCE_DATABASE_URL"])
    if target.database != TARGET_DATABASE:
        raise PreparationError("TARGET_DATABASE_URL must select explainit_icu.")
    if not target.drivername.startswith("postgresql") or not target.host:
        raise PreparationError("Expected a PostgreSQL target with an explicit host.")
    if (target.host, target.port or 5432) == (source.host, source.port or 5432):
        raise PreparationError("Source and target must use distinct servers for this migration.")
    return target.set(drivername="postgresql+psycopg2")


def check_empty_target(target):
    engine = create_engine(target, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            database, provider, locale = connection.execute(text("""
                SELECT current_database(), datlocprovider, datlocale
                FROM pg_database WHERE datname = current_database()
            """)).one()
            if (database, provider, locale) != (TARGET_DATABASE, "i", "en-US"):
                raise PreparationError("Connected database must be explainit_icu with ICU en-US.")
            tables = connection.execute(text("""
                SELECT count(*)
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
            """)).scalar_one()
            if tables:
                raise PreparationError(
                    f"Target public schema already has {tables} tables; review before rerunning."
                )
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-dir", type=Path, required=True,
        help="Path to the separate FastAPI repository containing alembic.ini.",
    )
    parser.add_argument(
        "--check-config", action="store_true",
        help="Check local configuration without connecting to any database.",
    )
    arguments = parser.parse_args()
    try:
        backend = arguments.backend_dir.expanduser().resolve()
        if not (backend / "alembic.ini").is_file():
            raise PreparationError("The supplied backend directory must contain alembic.ini.")
        target = load_target()
        if arguments.check_config:
            print(f"Configuration OK: {TARGET_DATABASE}, psycopg2, revision {REVISION}.")
            return 0
        check_empty_target(target)
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError):
        print("Configuration/target check failed. Verify the .env target and empty ICU database.", file=sys.stderr)
        return 1
    except SQLAlchemyError as error:
        print(f"Target check failed ({type(error).__name__}); check connectivity and database configuration.", file=sys.stderr)
        return 1

    child_environment = os.environ.copy()
    # This backend feeds the URL through ConfigParser before SQLAlchemy uses it.
    child_environment["ALEMBIC_DATABASE_URL"] = target.render_as_string(
        hide_password=False
    ).replace("%", "%%")
    print(f"Target verified empty: {TARGET_DATABASE}. Applying revision {REVISION}.", flush=True)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "alembic", "-c", str(backend / "alembic.ini"), "upgrade", REVISION],
        cwd=backend,
        env=child_environment,
        check=False,
    )
    if result.returncode == 0:
        print("Target schema preparation finished. Schema comparison is next.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
