import json
import pytest
from pathlib import Path
from unittest.mock import patch

FIXTURE = Path(__file__).parent / "fixtures" / "schema_registry.json"

def test_registry_has_required_fields():
    registry = json.loads(FIXTURE.read_text())
    for table, entry in registry.items():
        assert "description" in entry, f"{table} missing description"
        assert "tier" in entry, f"{table} missing tier"
        assert "columns" in entry, f"{table} missing columns"
        assert isinstance(entry["columns"], list), f"{table} columns must be list"
        for col in entry["columns"]:
            assert "name" in col, f"{table} column missing name"
            assert "type" in col, f"{table} column missing type"

def test_registry_tier_values():
    registry = json.loads(FIXTURE.read_text())
    valid_tiers = {"fact", "dim", "kpi", "route", "ops", "dq"}
    for table, entry in registry.items():
        assert entry["tier"] in valid_tiers, f"{table} has invalid tier: {entry['tier']}"


from analytics_agent.registry import load_registry, validate_registry, get_table_schema


def test_load_registry_parses_json():
    fixture = (Path(__file__).parent / "fixtures" / "schema_registry.json").read_text()
    with patch("pathlib.Path.read_text", return_value=fixture):
        registry = load_registry("tests/fixtures/schema_registry.json")
    assert "kpi_monthly_summary" in registry


def test_load_registry_raises_on_bad_json():
    with patch("pathlib.Path.read_text", return_value="{bad json"):
        with pytest.raises(ValueError, match="Failed to parse"):
            load_registry("schema_registry.json")


def test_validate_registry_passes_fixture():
    registry = json.loads((Path(__file__).parent / "fixtures" / "schema_registry.json").read_text())
    validate_registry(registry)  # should not raise


def test_validate_registry_raises_on_missing_description():
    registry = {"bad_table": {"tier": "kpi", "columns": []}}
    with pytest.raises(ValueError, match="bad_table"):
        validate_registry(registry)


def test_validate_s3_paths_raises_on_missing_table(monkeypatch):
    from analytics_agent.registry import validate_s3_paths
    registry = json.loads((Path(__file__).parent / "fixtures" / "schema_registry.json").read_text())

    def fake_list(Bucket, Prefix, MaxKeys):
        return {"Contents": []} if "kpi_monthly_summary" in Prefix else {"Contents": [{"Key": Prefix}]}

    monkeypatch.setattr("analytics_agent.registry._s3_list", fake_list)
    with pytest.raises(ValueError, match="kpi_monthly_summary"):
        validate_s3_paths(registry, bucket="test-bucket")


def test_get_table_schema_returns_slice():
    registry = json.loads((Path(__file__).parent / "fixtures" / "schema_registry.json").read_text())
    schema = get_table_schema(registry, "kpi_monthly_summary")
    assert schema["columns"][0]["name"] == "month"


def test_get_table_schema_raises_on_unknown():
    with pytest.raises(KeyError):
        get_table_schema({}, "unknown_table")
