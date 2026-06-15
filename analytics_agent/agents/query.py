import re


DDL_KEYWORDS = re.compile(
    r"\b(DROP|CREATE|INSERT|UPDATE|DELETE|ALTER|TRUNCATE)\b", re.IGNORECASE
)
FILE_FUNCTIONS = re.compile(
    r"\b(read_parquet|read_csv_auto|read_json|COPY|EXPORT|httpfs)\b", re.IGNORECASE
)


class SQLValidationError(Exception):
    pass


def validate_sql(sql: str, expected_table: str, known_tables: set[str]) -> None:
    stripped = sql.strip()

    # Check file functions first so COPY statements get the right error message
    if FILE_FUNCTIONS.search(stripped):
        raise SQLValidationError(
            "file function not allowed (read_parquet, httpfs, COPY, etc.)"
        )

    if not stripped.upper().startswith("SELECT"):
        raise SQLValidationError("SQL must start with SELECT")

    if DDL_KEYWORDS.search(stripped):
        raise SQLValidationError("DDL keywords not allowed")

    if ";" in stripped:
        raise SQLValidationError("chained statements not allowed (semicolon found)")

    found_tables = set(re.findall(r"\bFROM\s+(\w+)", stripped, re.IGNORECASE))
    found_tables |= set(re.findall(r"\bJOIN\s+(\w+)", stripped, re.IGNORECASE))
    for t in found_tables:
        if t.lower() != expected_table.lower():
            raise SQLValidationError(f"Table '{t}' not allowed — expected '{expected_table}'")


import signal
from dataclasses import dataclass
from analytics_agent.ollama_client import chat, strip_fences
from analytics_agent.registry import get_table_schema
from analytics_agent.config import S3_BUCKET, DUCKDB_TIMEOUT, ROW_CAP

QUERY_SYSTEM_PROMPT = """You are a SQL query agent for NYC yellow cab trip analytics stored in Parquet files on S3.

Rules:
- Write ONE SELECT statement only
- No markdown, no explanation, just the SQL
- Use only the table and columns provided
- UTC timestamps — do not convert timezone
- Revenue = total_fare_amount (excludes tips)
- Borough values: Manhattan, Brooklyn, Queens, Bronx, Staten Island
- Peak hours: 7-9 and 17-20 (24h)
- Do not use read_parquet(), httpfs, or any file functions"""


@dataclass
class QueryResult:
    sql: str
    rows: list[dict]
    capped: bool
    row_count_before_cap: int


class QueryError(Exception):
    pass


def _execute_duckdb(sql: str, table: str) -> list[dict]:
    import duckdb

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"DuckDB query exceeded {DUCKDB_TIMEOUT}s")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(DUCKDB_TIMEOUT)
    try:
        path = f"s3://{S3_BUCKET}/{table}/*.parquet"
        conn = duckdb.connect()
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute("SET s3_region='ap-southeast-1';")
        conn.execute("SET s3_use_credential_chain=true;")
        # Register the parquet path as a view so the validated SQL (which uses the
        # bare table name) runs unchanged — read_parquet() is never in the SQL string.
        conn.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
        result = conn.execute(sql).fetchdf()
        return result.to_dict(orient="records")
    finally:
        signal.alarm(0)


def run_query_agent(
    question: str,
    table: str,
    registry: dict,
    known_tables: set[str],
) -> QueryResult:
    schema = get_table_schema(registry, table)
    col_text = ", ".join(f"{c['name']} ({c['type']})" for c in schema["columns"])
    messages = [
        {"role": "system", "content": QUERY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Table: {table}\nColumns: {col_text}\n\nQuestion: {question}",
        },
    ]
    raw = chat(messages=messages)
    sql = strip_fences(raw)

    try:
        validate_sql(sql, table, known_tables)
    except SQLValidationError as e:
        raise QueryError(f"SQL validation failed: {e} | sql: {sql}")

    try:
        rows = _execute_duckdb(sql, table)
    except TimeoutError as e:
        raise QueryError(str(e))
    except Exception as e:
        raise QueryError(f"DuckDB error: {e}")

    before_cap = len(rows)
    capped = before_cap > ROW_CAP
    return QueryResult(
        sql=sql,
        rows=rows[:ROW_CAP],
        capped=capped,
        row_count_before_cap=before_cap,
    )
