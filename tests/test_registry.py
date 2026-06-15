import json, pytest
from pathlib import Path

FIXTURE = Path("tests/fixtures/schema_registry.json")

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
