#!/usr/bin/env python3
"""Scan local Parquet dirs and write schema_registry.json.

Usage: python scripts/build_registry.py --source docs/DB/files_list --output schema_registry.json
"""
import argparse
import json
import sys
from pathlib import Path
import pyarrow.parquet as pq

TIER_MAP = {
    "fact_": "fact", "dim_": "dim", "kpi_": "kpi",
    "route_": "route", "ops_": "ops", "dq_": "dq",
}

ID_COLUMN_SUFFIXES = ("_id", "_code")
DATE_TYPE_MARKERS = ("date", "timestamp")
DIMENSION_NAME_HINTS = {"year", "month", "day", "day_of_week", "week", "week_of_year", "quarter", "pickup_hour"}

CURATED_METADATA = {
    "kpi_zone_net_flow": {
        "description": "Zone-level pickup/dropoff imbalance and net flow metrics for NYC taxi zones.",
        "aliases": [
            "kpi zone net flow",
            "zone net flow",
            "net flow by zone",
            "zone inflow outflow",
        ],
        "grain": "one row per taxi zone",
        "use_for": [
            "zone pickup/dropoff imbalance",
            "zone net inflow and outflow analysis",
            "pickup revenue versus dropoff revenue by zone",
        ],
        "avoid_for": [
            "daily trend questions because this table has no date column",
            "hourly trend questions because this table has no hour column",
            "pickup-to-dropoff route pair questions because this table is zone-level, not route-pair grain",
        ],
        "example_questions": [
            "show table kpi zone net flow",
            "which zones have the largest pickup dropoff imbalance",
        ],
    },
}

def infer_tier(table_name: str) -> str:
    for prefix, tier in TIER_MAP.items():
        if table_name.startswith(prefix):
            return tier
    print(f"WARNING: no tier prefix matched for '{table_name}', defaulting to 'fact'", file=sys.stderr)
    return "fact"

def infer_column_roles(columns: list[dict]) -> dict:
    dimensions = []
    measures = []
    date_columns = []

    for column in columns:
        name = column["name"]
        name_lower = name.lower()
        type_lower = column["type"].lower()

        if any(marker in type_lower for marker in DATE_TYPE_MARKERS):
            date_columns.append(name)
            dimensions.append(name)
        elif (
            "string" in type_lower
            or "bool" in type_lower
            or name_lower.endswith(ID_COLUMN_SUFFIXES)
            or name_lower in DIMENSION_NAME_HINTS
        ):
            dimensions.append(name)
        elif any(marker in type_lower for marker in ("int", "float", "double", "decimal")):
            measures.append(name)
        else:
            dimensions.append(name)

    return {
        "dimensions": dimensions,
        "measures": measures,
        "date_columns": date_columns,
        "metadata_source": {
            "columns": "schema",
            "dimensions": "derived",
            "measures": "derived",
            "date_columns": "derived",
        },
    }

def apply_curated_metadata(table_name: str, entry: dict) -> dict:
    curated = CURATED_METADATA.get(table_name)
    if not curated:
        return entry

    result = {**entry, **curated}
    metadata_source = dict(entry.get("metadata_source", {}))
    for field in curated:
        metadata_source[field] = "curated"
    result["metadata_source"] = metadata_source
    return result

def scan_table(table_dir: Path) -> dict:
    parquet_files = list(table_dir.glob("*.parquet"))
    if not parquet_files:
        raise ValueError(f"No parquet files in {table_dir}")
    # All partition files share the same schema; reading the first is sufficient
    schema = pq.read_schema(parquet_files[0])
    columns = [
        {"name": field.name, "type": str(field.type)}
        for field in schema
    ]
    entry = {
        "description": f"{table_dir.name.replace('_', ' ').title()} — auto-generated, update manually",
        "tier": infer_tier(table_dir.name),
        "columns": columns,
        "example_questions": [],
        **infer_column_roles(columns),
    }
    return apply_curated_metadata(table_dir.name, entry)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="docs/DB/files_list")
    parser.add_argument("--output", default="schema_registry.json")
    args = parser.parse_args()

    source = Path(args.source)
    registry = {}
    failed = []
    for table_dir in sorted(source.iterdir()):
        if table_dir.is_dir():
            try:
                registry[table_dir.name] = scan_table(table_dir)
                print(f"  OK  {table_dir.name}")
            except Exception as e:
                print(f"  ERR {table_dir.name}: {e}")
                failed.append(table_dir.name)

    Path(args.output).write_text(json.dumps(registry, indent=2))
    print(f"\nWrote {len(registry)} tables to {args.output}")
    if failed:
        print(f"Failed tables: {failed}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
