#!/usr/bin/env python3
"""Upload Parquet files and schema_registry.json to S3.

Usage: python scripts/upload_to_s3.py --bucket nyc-taxi-analytics-dev --source docs/DB/files_list
       python scripts/upload_to_s3.py --bucket nyc-taxi-analytics-dev --source docs/DB/files_list --dry-run
"""
import argparse
import sys
import boto3
from pathlib import Path


def upload(bucket: str, source: Path, dry_run: bool = False) -> None:
    s3 = boto3.client("s3")

    registry = Path("schema_registry.json")
    if not registry.exists():
        raise FileNotFoundError(
            "schema_registry.json not found — run scripts/build_registry.py first"
        )
    _upload_file(s3, str(registry), bucket, "schema_registry.json", dry_run)

    for parquet_file in sorted(source.rglob("*.parquet")):
        key = str(parquet_file.relative_to(source))
        _upload_file(s3, str(parquet_file), bucket, key, dry_run)


def _upload_file(s3, local_path: str, bucket: str, key: str, dry_run: bool) -> None:
    if dry_run:
        print(f"  DRY  {key}")
    else:
        s3.upload_file(local_path, bucket, key)
        print(f"  OK   {key}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload NYC taxi Parquet data to S3")
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--source", default="docs/DB/files_list", help="Local Parquet directory")
    parser.add_argument("--dry-run", action="store_true", help="Print files without uploading")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"ERROR: source directory not found: {source}", file=sys.stderr)
        sys.exit(1)

    upload(args.bucket, source, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
