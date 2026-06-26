import os
from dotenv import load_dotenv

load_dotenv()

S3_BUCKET = os.environ["ANALYTICS_S3_BUCKET"]
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-4o-mini")
GPT_TIMEOUT = int(os.getenv("GPT_TIMEOUT_SECONDS", "60"))
DUCKDB_TIMEOUT = int(os.getenv("DUCKDB_TIMEOUT_SECONDS", "30"))
PIPELINE_TIMEOUT = int(os.getenv("PIPELINE_TIMEOUT_SECONDS", "180"))
ROW_CAP = int(os.getenv("ROW_CAP", "200"))
SCHEMA_REGISTRY_PATH = os.getenv("SCHEMA_REGISTRY_PATH", "schema_registry.json")
