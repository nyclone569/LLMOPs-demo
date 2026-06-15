#!/usr/bin/env python3
"""Scan local Parquet dirs and write schema_registry.json.

Usage: python scripts/build_registry.py --source docs/DB/files_list --output schema_registry.json
"""
import argparse, json
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
    return "fact"

def scan_table(table_dir: Path) -> dict:
    parquet_files = list(table_dir.glob("*.parquet"))
    if not parquet_files:
        raise ValueError(f"No parquet files in {table_dir}")
    schema = pq.read_schema(parquet_files[0])
    return {
        "description": f"{table_dir.name.replace('_', ' ').title()} — auto-generated, update manually",
        "tier": infer_tier(table_dir.name),
        "columns": [
            {"name": schema.field(i).name, "type": str(schema.field(i).type)}
            for i in range(len(schema))
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
    for table_dir in sorted(source.iterdir()):
        if table_dir.is_dir():
            try:
                registry[table_dir.name] = scan_table(table_dir)
                print(f"  OK  {table_dir.name}")
            except Exception as e:
                print(f"  ERR {table_dir.name}: {e}")

    Path(args.output).write_text(json.dumps(registry, indent=2))
    print(f"\nWrote {len(registry)} tables to {args.output}")

if __name__ == "__main__":
    main()
