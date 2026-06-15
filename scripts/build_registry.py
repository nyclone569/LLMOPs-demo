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

def infer_tier(table_name: str) -> str:
    for prefix, tier in TIER_MAP.items():
        if table_name.startswith(prefix):
            return tier
    print(f"WARNING: no tier prefix matched for '{table_name}', defaulting to 'fact'", file=sys.stderr)
    return "fact"

def scan_table(table_dir: Path) -> dict:
    parquet_files = list(table_dir.glob("*.parquet"))
    if not parquet_files:
        raise ValueError(f"No parquet files in {table_dir}")
    # All partition files share the same schema; reading the first is sufficient
    schema = pq.read_schema(parquet_files[0])
    return {
        "description": f"{table_dir.name.replace('_', ' ').title()} — auto-generated, update manually",
        "tier": infer_tier(table_dir.name),
        "columns": [
            {"name": field.name, "type": str(field.type)}
            for field in schema
        ],
        "example_questions": [],
    }

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
