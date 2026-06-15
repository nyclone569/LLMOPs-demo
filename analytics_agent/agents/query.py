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
