import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import _strip_fences, _validate_sql, SQLValidationError


def test_strip_fences_removes_sql_block():
    assert _strip_fences("```sql\nSELECT 1\n```") == "SELECT 1"


def test_strip_fences_removes_plain_block():
    assert _strip_fences("```\nSELECT 1\n```") == "SELECT 1"


def test_strip_fences_passthrough_plain_sql():
    assert _strip_fences("SELECT 1") == "SELECT 1"


def test_validate_sql_passes_valid_select():
    _validate_sql("SELECT trip_count FROM kpi_monthly_summary", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_ddl():
    with pytest.raises(SQLValidationError, match="DDL"):
        _validate_sql("SELECT 1; DROP TABLE kpi_monthly_summary", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_file_functions():
    with pytest.raises(SQLValidationError, match="file function"):
        _validate_sql("SELECT * FROM read_parquet('s3://...')", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_wrong_table():
    with pytest.raises(SQLValidationError, match="not allowed"):
        _validate_sql("SELECT * FROM some_other_table", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_non_select():
    with pytest.raises(SQLValidationError, match="SELECT"):
        _validate_sql("INSERT INTO foo VALUES (1)", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_unknown_expected_table():
    with pytest.raises(SQLValidationError, match="not in registry"):
        _validate_sql("SELECT 1 FROM bad_table", "bad_table", {"kpi_monthly_summary"})


def test_validate_sql_allows_cte():
    _validate_sql(
        "WITH cte AS (SELECT trip_count FROM kpi_monthly_summary) SELECT * FROM cte",
        "kpi_monthly_summary",
        {"kpi_monthly_summary"},
    )
