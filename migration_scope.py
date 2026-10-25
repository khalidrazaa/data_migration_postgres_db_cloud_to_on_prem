"""Track the applied trend/YouTube exclusion without changing schema evidence."""

from datetime import datetime, timezone
import json

from prepare_target_schema import PreparationError, WORKSPACE
from start_replication import PROJECT, PUBLICATION, SUBSCRIPTION, require


SCOPE_FILE = WORKSPACE / "replication_scope.json"
EXCLUDED_TABLES = frozenset(("public", name) for name in (
    "discovered_trends", "trend_ideas", "trend_items", "trend_videos", "volume_points",
))


def checked_original(original_tables):
    original = set(original_tables)
    require(len(original) == 24 and EXCLUDED_TABLES <= original,
            "Expected the original reviewed 24-table migration scope.")
    return original


def qualified(tables):
    return [".".join(name) for name in sorted(tables)]


def decoded_names(value):
    require(isinstance(value, list) and all(isinstance(name, str) for name in value),
            "Saved replication scope contains invalid table names.")
    require(len(value) == len(set(value)) and all(
        name.startswith("public.") and name.count(".") == 1 for name in value),
        "Saved replication scope contains invalid table names.")
    return {tuple(name.split(".", 1)) for name in value}


def effective_tables(original_tables):
    original = checked_original(original_tables)
    if not SCOPE_FILE.exists():
        return original
    try:
        saved = json.loads(SCOPE_FILE.read_text(encoding="utf-8"))
        require(isinstance(saved, dict) and saved.get("schema_version") == 1
                and saved.get("source_project") == PROJECT
                and saved.get("target_database") == "explainit"
                and saved.get("publication") == PUBLICATION
                and saved.get("subscription") == SUBSCRIPTION,
                "Saved replication scope does not identify this migration.")
        require(decoded_names(saved.get("original_application_tables")) == original
                and decoded_names(saved.get("excluded_tables")) == EXCLUDED_TABLES
                and decoded_names(saved.get("replicated_tables")) == original - EXCLUDED_TABLES,
                "Saved replication scope differs from the reviewed exclusion.")
    except (OSError, TypeError, ValueError) as error:
        raise PreparationError("Cannot read the saved replication scope; inspect replication_scope.json.") from error
    return original - EXCLUDED_TABLES


def write_applied_scope(original_tables):
    original = checked_original(original_tables)
    report = {
        "schema_version": 1,
        "source_project": PROJECT,
        "target_database": "explainit",
        "publication": PUBLICATION,
        "subscription": SUBSCRIPTION,
        "reason": "Trend and YouTube features will move to a separate project.",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "original_application_tables": qualified(original),
        "excluded_tables": qualified(EXCLUDED_TABLES),
        "replicated_tables": qualified(original - EXCLUDED_TABLES),
    }
    temporary = SCOPE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(SCOPE_FILE)
