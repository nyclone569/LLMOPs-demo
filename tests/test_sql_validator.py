import pytest
from analytics_agent.agents.query import validate_sql, SQLValidationError

KNOWN_TABLES = {"kpi_monthly_summary", "fact_trips_daily", "dim_zone"}


def test_valid_select_passes():
    validate_sql(
        "SELECT revenue FROM kpi_monthly_summary WHERE month = '2026-01-01'",
        "kpi_monthly_summary",
        KNOWN_TABLES,
    )


def test_rejects_non_select():
    with pytest.raises(SQLValidationError, match="must start with SELECT"):
        validate_sql("DROP TABLE kpi_monthly_summary", "kpi_monthly_summary", KNOWN_TABLES)


def test_rejects_ddl_in_select():
    with pytest.raises(SQLValidationError, match="DDL"):
        validate_sql(
            "SELECT * FROM kpi_monthly_summary; DROP TABLE kpi_monthly_summary",
            "kpi_monthly_summary",
            KNOWN_TABLES,
        )


def test_rejects_chained_statements():
    with pytest.raises(SQLValidationError, match="chained"):
        validate_sql("SELECT 1; SELECT 2", "kpi_monthly_summary", KNOWN_TABLES)


def test_rejects_wrong_table():
    with pytest.raises(SQLValidationError, match="not allowed"):
        validate_sql("SELECT * FROM fact_trips_daily", "kpi_monthly_summary", KNOWN_TABLES)


def test_rejects_read_parquet():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql(
            "SELECT * FROM read_parquet('s3://evil/path')",
            "kpi_monthly_summary",
            KNOWN_TABLES,
        )


def test_rejects_read_csv_auto():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql(
            "SELECT * FROM read_csv_auto('http://evil.com/data.csv')",
            "kpi_monthly_summary",
            KNOWN_TABLES,
        )


def test_rejects_copy():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql(
            "COPY (SELECT * FROM kpi_monthly_summary) TO '/tmp/out.csv'",
            "kpi_monthly_summary",
            KNOWN_TABLES,
        )


def test_rejects_unknown_table_reference():
    with pytest.raises(SQLValidationError, match="not allowed"):
        validate_sql("SELECT * FROM secret_table", "kpi_monthly_summary", KNOWN_TABLES)


def test_case_insensitive_select():
    validate_sql(
        "select revenue from kpi_monthly_summary",
        "kpi_monthly_summary",
        KNOWN_TABLES,
    )


def test_rejects_create():
    with pytest.raises(SQLValidationError):
        validate_sql(
            "CREATE TABLE evil AS SELECT * FROM kpi_monthly_summary",
            "kpi_monthly_summary",
            KNOWN_TABLES,
        )
