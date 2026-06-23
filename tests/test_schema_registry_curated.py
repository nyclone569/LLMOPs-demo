import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import _registry_as_prompt

REGISTRY_PATH = Path(__file__).parent.parent / "schema_registry.json"

CURATED_TABLES = [
    "route_top_pickup_zones",
    "kpi_zone_performance",
    "fact_trips_daily",
    "fact_trips_hourly",
    "fact_trips_hourly_zone",
    "fact_trips_borough",
    "kpi_daily_overview",
    "kpi_borough_comparison",
    "kpi_payment_trends",
]


def _load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text())


def test_curated_tables_have_required_metadata():
    registry = _load_registry()
    missing: list[str] = []
    for table in CURATED_TABLES:
        assert table in registry, f"{table} missing from schema_registry.json"
        entry = registry[table]
        for field in ("aliases", "use_for", "avoid_for", "example_questions"):
            value = entry.get(field)
            if not isinstance(value, list) or not value:
                missing.append(f"{table}.{field}")
        grain = entry.get("grain")
        if not isinstance(grain, str) or not grain.strip():
            missing.append(f"{table}.grain")
        description = entry.get("description", "")
        if "auto-generated" in description.lower():
            missing.append(f"{table}.description still auto-generated")
        metadata_source = entry.get("metadata_source", {})
        for flag in ("description", "aliases", "grain", "use_for", "avoid_for", "example_questions"):
            if metadata_source.get(flag) != "curated":
                missing.append(f"{table}.metadata_source.{flag} not marked curated")
    assert not missing, "Missing curated metadata: " + ", ".join(missing)


def test_curated_tables_appear_in_registry_prompt():
    registry = _load_registry()
    prompt = _registry_as_prompt(registry)
    for table in CURATED_TABLES:
        entry = registry[table]
        assert f"- {table}" in prompt, f"{table} not in registry prompt"
        assert f"grain: {entry['grain']}" in prompt, f"{table} grain not surfaced"
        for alias in entry["aliases"]:
            assert alias in prompt, f"{table} alias '{alias}' not surfaced"
        for use_for in entry["use_for"]:
            assert use_for in prompt, f"{table} use_for '{use_for}' not surfaced"
        for avoid_for in entry["avoid_for"]:
            assert avoid_for in prompt, f"{table} avoid_for '{avoid_for}' not surfaced"
