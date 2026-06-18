import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_registry import apply_curated_metadata, infer_column_roles


def test_infer_column_roles_identifies_dimensions_measures_and_dates():
    columns = [
        {"name": "pickup_date", "type": "date32[day]"},
        {"name": "location_id", "type": "int32"},
        {"name": "zone", "type": "string"},
        {"name": "trip_count", "type": "int64"},
        {"name": "total_revenue", "type": "double"},
        {"name": "pickup_hour", "type": "int64"},
    ]

    roles = infer_column_roles(columns)

    assert roles["date_columns"] == ["pickup_date"]
    assert "location_id" in roles["dimensions"]
    assert "zone" in roles["dimensions"]
    assert "trip_count" in roles["measures"]
    assert "total_revenue" in roles["measures"]
    assert "pickup_hour" in roles["dimensions"]


def test_apply_curated_metadata_adds_zone_net_flow_semantics():
    entry = {
        "description": "Kpi Zone Net Flow - auto-generated, update manually",
        "tier": "kpi",
        "columns": [
            {"name": "zone", "type": "string"},
            {"name": "borough", "type": "string"},
            {"name": "net_flow", "type": "int64"},
        ],
        "example_questions": [],
        "dimensions": ["zone", "borough"],
        "measures": ["net_flow"],
        "date_columns": [],
    }

    result = apply_curated_metadata("kpi_zone_net_flow", entry)

    assert result["description"].startswith("Zone-level pickup/dropoff imbalance")
    assert "kpi zone net flow" in result["aliases"]
    assert result["grain"] == "one row per taxi zone"
    assert "zone pickup/dropoff imbalance" in result["use_for"]
    assert any("no date column" in item for item in result["avoid_for"])
