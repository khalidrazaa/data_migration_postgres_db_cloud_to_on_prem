"""Compare PostgreSQL 17 public application schemas using read-only snapshots.

This compares tables, columns, constraints, indexes, enums, sequences and Alembic
revisions. It does not establish equality of data, grants, RLS policies, functions,
triggers, views, extensions or other schemas. No sequence state is read.
"""

from datetime import datetime, timezone
import json
import sys

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from prepare_target_schema import (
    PreparationError, WORKSPACE, database_error_detail, load_target,
)


OUTPUT = WORKSPACE / "schema_comparison.json"
PUBLIC_TABLE = "n.nspname = 'public' AND c.relkind IN ('r', 'p')"
METADATA_TABLES = {("public", "alembic_version"), ("public", "schema_migrations")}
QUERIES = {
    "tables": f"""
        SELECT n.nspname AS schema, c.relname AS name, c.relkind AS kind,
               c.relpersistence AS persistence,
               pg_get_partkeydef(c.oid) AS partition_key,
               pg_get_expr(c.relpartbound, c.oid, false) AS partition_bound,
               ARRAY(SELECT format('%I.%I', pn.nspname, pc.relname)
                     FROM pg_inherits h JOIN pg_class pc ON pc.oid = h.inhparent
                     JOIN pg_namespace pn ON pn.oid = pc.relnamespace
                     WHERE h.inhrelid = c.oid
                     ORDER BY pn.nspname, pc.relname) AS parents
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE {PUBLIC_TABLE} ORDER BY n.nspname, c.relname
    """,
    "columns": f"""
        SELECT n.nspname AS schema, c.relname AS table, a.attname AS name,
               row_number() OVER (PARTITION BY c.oid ORDER BY a.attnum) AS position,
               format_type(a.atttypid, a.atttypmod) AS type,
               a.attnotnull AS not_null, a.attidentity AS identity,
               a.attgenerated AS generated,
               pg_get_expr(d.adbin, d.adrelid, false) AS default,
               cn.nspname AS collation_schema, co.collname AS collation
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
        LEFT JOIN pg_collation co ON co.oid = a.attcollation
        LEFT JOIN pg_namespace cn ON cn.oid = co.collnamespace
        WHERE {PUBLIC_TABLE} AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY n.nspname, c.relname, a.attnum
    """,
    "constraints": f"""
        SELECT n.nspname AS schema, c.relname AS table, con.conname AS name,
               con.contype AS type, pg_get_constraintdef(con.oid, false) AS definition,
               con.convalidated AS validated, con.condeferrable AS deferrable,
               con.condeferred AS initially_deferred, con.connoinherit AS no_inherit
        FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE {PUBLIC_TABLE} ORDER BY n.nspname, c.relname, con.conname
    """,
    "indexes": f"""
        SELECT n.nspname AS schema, c.relname AS table, ic.relname AS name,
               pg_get_indexdef(i.indexrelid, 0, false) AS definition,
               i.indisvalid AS valid, i.indisready AS ready, i.indislive AS live
        FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_class ic ON ic.oid = i.indexrelid
        WHERE {PUBLIC_TABLE} ORDER BY n.nspname, c.relname, ic.relname
    """,
    "enums": """
        SELECT n.nspname AS schema, t.typname AS name,
               coalesce(array_agg(e.enumlabel::text ORDER BY e.enumsortorder)
                        FILTER (WHERE e.oid IS NOT NULL), ARRAY[]::text[]) AS labels
        FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        LEFT JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE n.nspname = 'public' AND t.typtype = 'e'
        GROUP BY n.nspname, t.typname ORDER BY n.nspname, t.typname
    """,
    "sequences": """
        SELECT n.nspname AS schema, c.relname AS name,
               format_type(s.seqtypid, NULL) AS type, s.seqstart AS start,
               s.seqincrement AS increment, s.seqmin AS minimum,
               s.seqmax AS maximum, s.seqcache AS cache, s.seqcycle AS cycle,
               c.relpersistence AS persistence,
               tn.nspname AS owner_schema, tc.relname AS owner_table,
               a.attname AS owner_column, d.deptype AS ownership_kind
        FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_depend d ON d.classid = 'pg_class'::regclass
             AND d.objid = c.oid AND d.objsubid = 0
             AND d.refclassid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
        LEFT JOIN pg_class tc ON tc.oid = d.refobjid
        LEFT JOIN pg_namespace tn ON tn.oid = tc.relnamespace
        LEFT JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = d.refobjsubid
        WHERE n.nspname = 'public' ORDER BY n.nspname, c.relname
    """,
}
KEYS = {
    "tables": ("schema", "name"),
    "columns": ("schema", "table", "name"),
    "constraints": ("schema", "table", "name"),
    "indexes": ("schema", "table", "name"),
    "enums": ("schema", "name"),
    "sequences": ("schema", "name"),
}


def rows(connection, sql):
    return [dict(row) for row in connection.execute(text(sql)).mappings()]


def snapshot(url, *, populated_tables=False):
    engine = create_engine(url, connect_args={"connect_timeout": 10})
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL search_path = pg_catalog")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '30s'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
            metadata = rows(connection, """
                SELECT current_database() AS database,
                       current_setting('server_version') AS server_version,
                       current_setting('server_version_num')::int AS server_version_num,
                       current_setting('TimeZone') AS session_timezone,
                       pg_encoding_to_char(encoding) AS encoding,
                       datlocprovider AS locale_provider, datlocale AS provider_locale,
                       datcollate AS lc_collate, datctype AS lc_ctype,
                       datcollversion AS collation_version
                FROM pg_database WHERE datname = current_database()
            """)[0]
            result = {
                "collected_at_utc": datetime.now(timezone.utc).isoformat(),
                "database_metadata": metadata,
                "schema": {name: rows(connection, sql) for name, sql in QUERIES.items()},
            }
            tables = result["schema"]["tables"]
            has_revision = any(t["name"] == "alembic_version" for t in tables)
            result["alembic_revisions"] = (
                [r["version_num"] for r in rows(connection,
                 'SELECT version_num FROM public.alembic_version ORDER BY version_num')]
                if has_revision else None
            )
            if populated_tables:
                quote = engine.dialect.identifier_preparer.quote_identifier
                result["target_table_nonempty"] = {
                    f'{table["schema"]}.{table["name"]}': connection.exec_driver_sql(
                        f'SELECT EXISTS (SELECT 1 FROM {quote(table["schema"])}.'
                        f'{quote(table["name"])} LIMIT 1)'
                    ).scalar_one()
                    for table in tables
                }
            return result
    finally:
        engine.dispose()


def compare(source, target):
    differences = []
    for category, fields in KEYS.items():
        source_objects = {
            tuple(row[field] for field in fields): row
            for row in source["schema"][category]
        }
        target_objects = {
            tuple(row[field] for field in fields): row
            for row in target["schema"][category]
        }
        for key in sorted(source_objects.keys() | target_objects.keys()):
            before, after = source_objects.get(key), target_objects.get(key)
            if before != after:
                differences.append({
                    "category": category, "object": ".".join(key),
                    "source": before, "target": after,
                })
    before, after = source["alembic_revisions"], target["alembic_revisions"]
    if before != after or not before or not after:
        differences.append({
            "category": "alembic_revisions", "object": "public.alembic_version",
            "source": before, "target": after,
        })
    for label, data in (("source", source), ("target", target)):
        for index in data["schema"]["indexes"]:
            if not all(index[flag] for flag in ("valid", "ready", "live")):
                differences.append({
                    "category": "index_health", "object": f'{label}:{index["name"]}',
                    "detail": "Index must be valid, ready and live.",
                })
    return differences


def review_reason(difference):
    """Classify only reviewed metadata and column-position differences as advisory."""
    category = difference["category"]
    before, after = difference.get("source"), difference.get("target")
    if category in ("tables", "columns", "constraints", "indexes"):
        name_field = "name" if category == "tables" else "table"
        objects = [row for row in (before, after) if row is not None]
        if objects and all((row["schema"], row[name_field]) in METADATA_TABLES for row in objects):
            # schema_migrations tracks the reviewed SQL runner, not application data.
            # Alembic revision values remain a separate, blocking comparison.
            return "Migration bookkeeping definition; revision values are checked separately."
    if category == "columns" and before is not None and after is not None:
        if ({key: value for key, value in before.items() if key != "position"}
                == {key: value for key, value in after.items() if key != "position"}):
            # PostgreSQL logical replication matches columns by name, not position:
            # https://www.postgresql.org/docs/17/logical-replication-subscription.html
            # Keep this difference for review of any positional SQL or COPY usage.
            return "Column position only; logical replication matches columns by name."
    return None


def main():
    active_url = None
    active_database = "configuration"
    try:
        target_url = load_target()
        settings = dotenv_values(WORKSPACE / ".env", interpolate=False)
        source_url = make_url(settings["SOURCE_DATABASE_URL"])
        if not source_url.drivername.startswith("postgresql") or not source_url.host:
            raise PreparationError("Expected a PostgreSQL source with an explicit host.")
        source_url = source_url.set(drivername="postgresql+psycopg2")
        active_database, active_url = "source", source_url
        source = snapshot(source_url)
        active_database, active_url = "target", target_url
        target = snapshot(target_url, populated_tables=True)
        differences = compare(source, target)
        application_differences = [item for item in differences if review_reason(item) is None]
        source_tables = {(row["schema"], row["name"]) for row in source["schema"]["tables"]}
        target_tables = {(row["schema"], row["name"]) for row in target["schema"]["tables"]}
        application_tables = [".".join(key) for key in sorted(
            (source_tables & target_tables) - METADATA_TABLES
        )]
        report = {
            "scope": __doc__.strip(),
            "snapshot_note": "Independent read-only snapshots; concurrent DDL requires rechecking.",
            "source": source, "target": target, "differences": differences,
            "schema_and_revisions_match": not differences,
            "application_tables": application_tables,
            "application_schema_differences": application_differences,
            "application_schema_matches": not application_differences,
            "reviewed_differences": [dict(item, review_reason=review_reason(item))
                                     for item in differences if review_reason(item) is not None],
        }
        OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except PreparationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except SQLAlchemyError as error:
        print(f"Schema comparison failed for {active_database} ({type(error).__name__}).", file=sys.stderr)
        detail = database_error_detail(error, active_url)
        if detail:
            print(detail, file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, OSError):
        print("Schema comparison failed. Check configuration and the local report path.", file=sys.stderr)
        return 1

    print("Scope: public tables, columns, constraints, indexes, enums, sequences and Alembic revisions.")
    print("Data equality, grants, RLS policies, functions, triggers, views and extensions are outside scope.")
    for label, data in (("Source", source), ("Target", target)):
        print(f"{label} database metadata: {json.dumps(data['database_metadata'], ensure_ascii=False)}")
        print(f"{label} objects: " + ", ".join(
            f"{len(data['schema'][name])} {name}" for name in QUERIES
        ))
        print(f"{label} Alembic revisions: {data['alembic_revisions']}")
    print("Database version/locale metadata is informational and does not determine the schema result.")
    print("Target populated tables: " + ", ".join(
        name for name, nonempty in target["target_table_nonempty"].items() if nonempty
    ))
    for difference in differences:
        reason = review_reason(difference)
        if reason:
            print(f"Review [{difference['category']}] {difference['object']}: {reason}")
            continue
        print(f"Difference [{difference['category']}] {difference['object']}: "
              + json.dumps({k: v for k, v in difference.items()
                            if k not in ("category", "object")}, ensure_ascii=False))
    print(f"Exact schema comparison: {'MATCH' if not differences else 'DIFFERENT'} "
          f"({len(differences)} recorded differences).")
    print(f"Application definitions and Alembic revisions: "
          f"{'MATCH' if not application_differences else 'MISMATCH'} "
          f"({len(application_tables)} common application tables; "
          f"{len(application_differences)} blocking differences). Report: {OUTPUT.name}")
    print("Snapshots are independent; rerun if source DDL occurs before cutover.")
    return 0 if not application_differences else 2


if __name__ == "__main__":
    raise SystemExit(main())
