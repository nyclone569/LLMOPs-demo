import json
import boto3
from pathlib import Path


def load_registry(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse schema registry at {path}: {e}")


def validate_registry(registry: dict) -> None:
    required = {"description", "tier", "columns"}
    valid_tiers = {"fact", "dim", "kpi", "route", "ops", "dq"}
    for table, entry in registry.items():
        missing = required - entry.keys()
        if missing:
            raise ValueError(f"{table} missing {missing}")
        if entry["tier"] not in valid_tiers:
            raise ValueError(f"{table} has invalid tier: {entry['tier']}")


def _s3_list(Bucket: str, Prefix: str, MaxKeys: int) -> dict:
    s3 = boto3.client("s3")
    return s3.list_objects_v2(Bucket=Bucket, Prefix=Prefix, MaxKeys=MaxKeys)


def validate_s3_paths(registry: dict, bucket: str) -> None:
    missing = []
    for table in registry:
        resp = _s3_list(Bucket=bucket, Prefix=f"{table}/", MaxKeys=1)
        if not resp.get("Contents"):
            missing.append(table)
    if missing:
        raise ValueError(f"Tables missing from S3 bucket '{bucket}': {missing}")


def get_table_schema(registry: dict, table: str) -> dict:
    if table not in registry:
        raise KeyError(f"unknown_table: {table} not in registry")
    return registry[table]


def registry_as_prompt_text(registry: dict) -> str:
    lines = []
    for table, entry in registry.items():
        col_list = ", ".join(f"{c['name']}({c['type']})" for c in entry["columns"])
        examples = "; ".join(entry.get("example_questions", []))
        lines.append(
            f"- {table} [{entry['tier']}]: {entry['description']} | columns: {col_list} | examples: {examples}"
        )
    return "\n".join(lines)
