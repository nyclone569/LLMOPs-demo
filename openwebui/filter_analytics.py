"""
title: NYC Taxi Analytics Pipe
author: llmops
version: 1.0.0
license: MIT
requirements:
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pydantic import BaseModel
from starlette.responses import StreamingResponse
from typing import Optional
import httpx
import json
import re
import traceback

DOMAIN_TERMS = {
    "taxi", "trip", "trips", "fare", "borough", "zone", "pickup", "dropoff",
    "vendor", "route", "revenue", "passenger", "passengers", "yellow", "green",
    "fhv", "manhattan", "brooklyn", "queens", "bronx", "staten island",
}

ANALYTICS_WORDS = {
    "how many", "average", "total", "compare", "top", "trend", "count",
    "per", "rate", "show", "summary", "breakdown", "most", "least", "peak",
    "weekly", "monthly", "daily", "hourly",
}

INTENT_ANALYTICS = "analytics"
INTENT_AMBIGUOUS = "ambiguous"
INTENT_CHAT = "chat"


def classify_intent(message: str) -> str:
    """Three-tier intent classification based on domain + analytics signal counts."""
    lower = message.lower()

    domain_count = sum(
        1 for term in DOMAIN_TERMS
        if re.search(rf'\b{re.escape(term)}\b', lower)
    )

    analytics_count = sum(
        1 for word in ANALYTICS_WORDS
        if re.search(rf'\b{re.escape(word)}\b', lower)
    )

    if domain_count >= 1 and analytics_count >= 1:
        return INTENT_ANALYTICS
    if domain_count >= 1:
        return INTENT_AMBIGUOUS
    return INTENT_CHAT


def chart_spec_to_vegalite(chart_spec: dict, rows: list[dict]) -> dict:
    """Convert summarize agent's custom chart_spec to a Vega-Lite spec."""
    chart_type = chart_spec.get("type", "bar")
    x_field = chart_spec["x"]
    y_field = chart_spec["y"]

    if chart_type == "line":
        mark = "line"
    else:
        mark = "bar"  # bar and pie both render as bar

    x_orient = {}
    if chart_type == "pie":
        x_orient = {"sort": "-y"}

    if chart_type == "line":
        sample_val = str(rows[0].get(x_field, "")) if rows else ""
        x_type = "temporal" if re.match(r"\d{4}-\d{2}", sample_val) else "quantitative"
    else:
        x_type = "ordinal"

    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": mark,
        "data": {"values": rows},
        "encoding": {
            "x": {"field": x_field, "type": x_type, **x_orient},
            "y": {"field": y_field, "type": "quantitative"},
        },
        "width": "container",
        "height": 300,
    }


_DDL_KEYWORDS = re.compile(
    r"\b(DROP|CREATE|INSERT|UPDATE|DELETE|ALTER|TRUNCATE)\b", re.IGNORECASE
)
_FILE_FUNCTIONS = re.compile(
    r"\b(read_parquet|read_csv_auto|read_json|COPY|EXPORT|httpfs)\b", re.IGNORECASE
)


class SQLValidationError(Exception):
    pass


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:sql)?\s*\n?(.*?)\n?```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


def _validate_sql(sql: str, expected_table: str, known_tables: set) -> None:
    stripped = sql.strip().rstrip(";").strip()
    if _FILE_FUNCTIONS.search(stripped):
        raise SQLValidationError("file function not allowed (read_parquet, httpfs, COPY, etc.)")
    leading = stripped.upper().lstrip()
    if not (leading.startswith("SELECT") or leading.startswith("WITH")):
        raise SQLValidationError("SQL must start with SELECT or WITH")
    if _DDL_KEYWORDS.search(stripped):
        raise SQLValidationError("DDL keywords not allowed")
    if ";" in stripped:
        raise SQLValidationError("chained statements not allowed")
    if expected_table not in known_tables:
        raise SQLValidationError(f"Table '{expected_table}' not in registry")
    found = set(re.findall(r"\bFROM\s+(\w+)", stripped, re.IGNORECASE))
    found |= set(re.findall(r"\bJOIN\s+(\w+)", stripped, re.IGNORECASE))
    # CTE names are valid references — exclude them from the foreign-table check
    cte_names = {m.lower() for m in re.findall(r"\bWITH\s+(\w+)\s+AS\s*\(", stripped, re.IGNORECASE)}
    for t in found:
        if t.lower() != expected_table.lower() and t.lower() not in cte_names:
            raise SQLValidationError(f"Table '{t}' not allowed — expected '{expected_table}'")


def build_html_artifact(chart_spec: dict, rows: list[dict]) -> str | None:
    """Wrap a Vega-Lite spec in a self-contained HTML artifact string.

    Returns None for table type (text-only result).
    """
    if chart_spec.get("type") == "table":
        return None

    vl_spec = chart_spec_to_vegalite(chart_spec, rows)
    spec_json = json.dumps(vl_spec)
    spec_json = spec_json.replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html>
<head>
  <script src="https://cdn.jsdelivr.net/npm/vega@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
  <style>body {{ margin: 0; }} #chart {{ width: 100%; }}</style>
</head>
<body>
  <div id="chart"></div>
  <script>
    vegaEmbed('#chart', {spec_json}, {{actions: false}});
  </script>
</body>
</html>"""


LITELLM_URL = "http://litellm.litellm.svc.cluster.local:4000/v1/chat/completions"
LITELLM_MODEL = "private-chat"
LITELLM_TIMEOUT = 60


def _llm_chat(messages: list[dict], model: str = LITELLM_MODEL, litellm_url: str = LITELLM_URL, api_key: str = "") -> str:
    """HTTP call to LiteLLM OpenAI-compatible endpoint."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    resp = httpx.post(
        litellm_url,
        json={"model": model, "messages": messages},
        headers=headers,
        timeout=LITELLM_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def _stream_llm(messages: list[dict], litellm_url: str = LITELLM_URL, model: str = LITELLM_MODEL, api_key: str = "") -> StreamingResponse:
    """Stream LiteLLM response as SSE bytes."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def generator():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                litellm_url,
                json={"model": model, "messages": messages, "stream": True},
                headers=headers,
                timeout=LITELLM_TIMEOUT,
            ) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(generator(), media_type="text/event-stream")


# Registry bundled as constant — matches schema_registry.json at repo root.
REGISTRY: dict = {'dim_date': {'description': 'Dim Date — auto-generated, update manually', 'tier': 'dim', 'columns': [{'name': 'date', 'type': 'date32[day]'}, {'name': 'year', 'type': 'int64'}, {'name': 'month', 'type': 'int64'}, {'name': 'day', 'type': 'int64'}, {'name': 'day_of_week', 'type': 'int64'}, {'name': 'is_weekend', 'type': 'bool'}, {'name': 'is_holiday', 'type': 'bool'}, {'name': 'quarter', 'type': 'int64'}, {'name': 'week_of_year', 'type': 'int64'}], 'example_questions': []}, 'dim_payment_type': {'description': 'Dim Payment Type — auto-generated, update manually', 'tier': 'dim', 'columns': [{'name': 'payment_type_code', 'type': 'int32'}, {'name': 'description', 'type': 'string'}], 'example_questions': []}, 'dim_rate_code': {'description': 'Dim Rate Code — auto-generated, update manually', 'tier': 'dim', 'columns': [{'name': 'rate_code_id', 'type': 'int32'}, {'name': 'description', 'type': 'string'}], 'example_questions': []}, 'dim_vendor': {'description': 'Dim Vendor — auto-generated, update manually', 'tier': 'dim', 'columns': [{'name': 'vendor_id', 'type': 'int32'}, {'name': 'vendor_name', 'type': 'string'}], 'example_questions': []}, 'dim_zone': {'description': 'Dim Zone — auto-generated, update manually', 'tier': 'dim', 'columns': [{'name': 'location_id', 'type': 'int32'}, {'name': 'borough', 'type': 'string'}, {'name': 'zone', 'type': 'string'}, {'name': 'service_zone', 'type': 'string'}], 'example_questions': []}, 'dim_zone_grouped': {'description': 'Dim Zone Grouped — auto-generated, update manually', 'tier': 'dim', 'columns': [{'name': 'location_id', 'type': 'int32'}, {'name': 'zone', 'type': 'string'}, {'name': 'borough', 'type': 'string'}, {'name': 'service_zone', 'type': 'string'}, {'name': 'pickup_trip_count', 'type': 'int64'}, {'name': 'trip_volume_tier', 'type': 'string'}, {'name': 'group_name', 'type': 'string'}], 'example_questions': []}, 'dq_batch_metadata': {'description': 'Dq Batch Metadata — auto-generated, update manually', 'tier': 'dq', 'columns': [{'name': 'script_name', 'type': 'string'}, {'name': 'export_timestamp', 'type': 'timestamp[ns]'}, {'name': 'export_date', 'type': 'date32[day]'}, {'name': 'fact_trips_row_count', 'type': 'int64'}, {'name': 'dataset_count', 'type': 'int32'}], 'example_questions': []}, 'dq_row_count_trend': {'description': 'Dq Row Count Trend — auto-generated, update manually', 'tier': 'dq', 'columns': [{'name': 'pickup_date', 'type': 'date32[day]'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'delta_from_7day_avg', 'type': 'double'}, {'name': 'anomaly_flag', 'type': 'string'}], 'example_questions': []}, 'dq_validation_summary': {'description': 'Dq Validation Summary — auto-generated, update manually', 'tier': 'dq', 'columns': [{'name': 'pickup_date', 'type': 'date32[day]'}, {'name': 'total_trips', 'type': 'int64'}, {'name': 'zero_distance', 'type': 'int64'}, {'name': 'negative_fare', 'type': 'int64'}, {'name': 'invalid_passengers', 'type': 'int64'}, {'name': 'negative_tip', 'type': 'int64'}, {'name': 'total_less_than_fare', 'type': 'int64'}], 'example_questions': []}, 'fact_trips_borough': {'description': 'Fact Trips Borough — auto-generated, update manually', 'tier': 'fact', 'columns': [{'name': 'pickup_date', 'type': 'date32[day]'}, {'name': 'pickup_borough', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}], 'example_questions': []}, 'fact_trips_daily': {'description': 'Fact Trips Daily — auto-generated, update manually', 'tier': 'fact', 'columns': [{'name': 'pickup_date', 'type': 'date32[day]'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'total_revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_tip_pct', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'total_passengers', 'type': 'int64'}], 'example_questions': []}, 'fact_trips_hourly': {'description': 'Hourly trip counts and fares aggregated across all zones', 'tier': 'fact', 'columns': [{'name': 'pickup_date', 'type': 'date32[day]'}, {'name': 'pickup_hour', 'type': 'int64'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}], 'example_questions': ['which hour has the most trips', 'peak hour revenue']}, 'fact_trips_hourly_zone': {'description': 'Fact Trips Hourly Zone — auto-generated, update manually', 'tier': 'fact', 'columns': [{'name': 'pickup_date', 'type': 'date32[day]'}, {'name': 'pickup_hour', 'type': 'int64'}, {'name': 'pickup_zone', 'type': 'string'}, {'name': 'pickup_borough', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'total_revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'dropoff_count', 'type': 'int64'}], 'example_questions': []}, 'kpi_borough_comparison': {'description': 'Revenue and trip counts broken down by NYC borough', 'tier': 'kpi', 'columns': [{'name': 'pickup_borough', 'type': 'string'}, {'name': 'trips', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'market_share_pct', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}], 'example_questions': ['revenue by borough', 'which borough has most trips']}, 'kpi_daily_overview': {'description': 'Daily revenue, trips, and AOV for recent days', 'tier': 'kpi', 'columns': [{'name': 'pickup_date', 'type': 'date32[day]'}, {'name': 'trips', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_tip_pct', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'unique_vendors', 'type': 'int64'}, {'name': 'utilization_rate', 'type': 'decimal128(23, 1)'}], 'example_questions': ['daily overview', 'recent days summary']}, 'kpi_monthly_summary': {'description': 'Monthly aggregated revenue, trips, and AOV across all zones', 'tier': 'kpi', 'columns': [{'name': 'pickup_year', 'type': 'int32'}, {'name': 'pickup_month', 'type': 'int32'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'total_revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'avg_trip_per_day', 'type': 'decimal128(22, 1)'}, {'name': 'prev_month_revenue', 'type': 'double'}, {'name': 'mom_growth_pct', 'type': 'double'}], 'example_questions': ['show monthly revenue trend', 'which month had the most trips']}, 'kpi_payment_trends': {'description': 'Payment type breakdown (cash, card, etc.) by period', 'tier': 'kpi', 'columns': [{'name': 'payment_type', 'type': 'int32'}, {'name': 'payment_desc', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_tip_pct', 'type': 'double'}], 'example_questions': ['payment type breakdown', 'how do passengers pay']}, 'kpi_vendor_performance': {'description': 'Trip count and revenue by taxi vendor', 'tier': 'kpi', 'columns': [{'name': 'vendor_id', 'type': 'int32'}, {'name': 'vendor_name', 'type': 'string'}, {'name': 'trips', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'market_share_pct', 'type': 'decimal128(24, 1)'}], 'example_questions': ['vendor performance', 'which vendor has most trips']}, 'kpi_weekly_trends': {'description': 'Weekly revenue, trip count, and AOV trends', 'tier': 'kpi', 'columns': [{'name': 'year', 'type': 'int64'}, {'name': 'week', 'type': 'int64'}, {'name': 'week_start', 'type': 'date32[day]'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'prev_week_trips', 'type': 'int64'}, {'name': 'trip_growth_pct', 'type': 'decimal128(23, 1)'}, {'name': 'revenue_growth_pct', 'type': 'double'}], 'example_questions': ['show weekly trip count', 'weekly revenue trend']}, 'kpi_zone_net_flow': {'description': 'Kpi Zone Net Flow — auto-generated, update manually', 'tier': 'kpi', 'columns': [{'name': 'zone', 'type': 'string'}, {'name': 'borough', 'type': 'string'}, {'name': 'pickups', 'type': 'int64'}, {'name': 'dropoffs', 'type': 'int64'}, {'name': 'net_flow', 'type': 'int64'}, {'name': 'net_flow_ratio', 'type': 'decimal128(22, 1)'}, {'name': 'imbalance_score', 'type': 'decimal128(22, 1)'}, {'name': 'primary_inflow_source', 'type': 'string'}, {'name': 'primary_outflow_dest', 'type': 'string'}, {'name': 'pickup_revenue', 'type': 'double'}, {'name': 'dropoff_revenue', 'type': 'double'}], 'example_questions': []}, 'kpi_zone_performance': {'description': 'Revenue, trips, and AOV per zone', 'tier': 'kpi', 'columns': [{'name': 'location_id', 'type': 'int32'}, {'name': 'zone', 'type': 'string'}, {'name': 'borough', 'type': 'string'}, {'name': 'pickups', 'type': 'int64'}, {'name': 'dropoffs', 'type': 'int64'}, {'name': 'net_flow', 'type': 'int64'}, {'name': 'net_flow_ratio', 'type': 'decimal128(22, 1)'}, {'name': 'pickup_revenue', 'type': 'double'}, {'name': 'dropoff_revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_tip_pct', 'type': 'double'}, {'name': 'airport_trip_count', 'type': 'int64'}, {'name': 'airport_trip_pct', 'type': 'decimal128(24, 1)'}], 'example_questions': ['zone performance by revenue', 'top zones by fare']}, 'od_borough_matrix': {'description': 'Od Borough Matrix — auto-generated, update manually', 'tier': 'fact', 'columns': [{'name': 'pickup_borough', 'type': 'string'}, {'name': 'dropoff_borough', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'total_revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'pct_of_total', 'type': 'decimal128(24, 1)'}], 'example_questions': []}, 'ops_passenger_count_pattern': {'description': 'Ops Passenger Count Pattern — auto-generated, update manually', 'tier': 'ops', 'columns': [{'name': 'passenger_count', 'type': 'int32'}, {'name': 'pickup_hour', 'type': 'int64'}, {'name': 'pickup_borough', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}], 'example_questions': []}, 'ops_peak_hours_heatmap': {'description': 'Trip count by hour-of-day and day-of-week for heatmap display', 'tier': 'ops', 'columns': [{'name': 'pickup_hour', 'type': 'int64'}, {'name': 'day_of_week', 'type': 'int64'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}], 'example_questions': ['show peak hour heatmap', 'busy hours by day']}, 'ops_trip_distance_distribution': {'description': 'Ops Trip Distance Distribution — auto-generated, update manually', 'tier': 'ops', 'columns': [{'name': 'distance_bucket', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_total', 'type': 'double'}], 'example_questions': []}, 'ops_utilization_rate': {'description': 'Ops Utilization Rate — auto-generated, update manually', 'tier': 'ops', 'columns': [{'name': 'pickup_date', 'type': 'date32[day]'}, {'name': 'total_trips', 'type': 'int64'}, {'name': 'tipped_trips', 'type': 'int64'}, {'name': 'tip_rate_pct', 'type': 'decimal128(24, 1)'}, {'name': 'multi_passenger_trips', 'type': 'int64'}, {'name': 'multi_passenger_pct', 'type': 'decimal128(24, 1)'}, {'name': 'avg_passengers', 'type': 'double'}], 'example_questions': []}, 'route_airport_analysis': {'description': 'Route Airport Analysis — auto-generated, update manually', 'tier': 'route', 'columns': [{'name': 'direction', 'type': 'string'}, {'name': 'airport', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'avg_tip_pct', 'type': 'double'}], 'example_questions': []}, 'route_airport_zone_matrix': {'description': 'Route Airport Zone Matrix — auto-generated, update manually', 'tier': 'route', 'columns': [{'name': 'airport_zone', 'type': 'string'}, {'name': 'residential_zone', 'type': 'string'}, {'name': 'borough', 'type': 'string'}, {'name': 'trips', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'peak_hour', 'type': 'int32'}, {'name': 'avg_tip', 'type': 'double'}], 'example_questions': []}, 'route_cross_borough': {'description': 'Route Cross Borough — auto-generated, update manually', 'tier': 'route', 'columns': [{'name': 'pickup_borough', 'type': 'string'}, {'name': 'dropoff_borough', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}], 'example_questions': []}, 'route_popular_routes': {'description': 'Most frequent pickup-to-dropoff zone pairs by trip count', 'tier': 'route', 'columns': [{'name': 'pickup_zone', 'type': 'string'}, {'name': 'pickup_borough', 'type': 'string'}, {'name': 'dropoff_zone', 'type': 'string'}, {'name': 'dropoff_borough', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_revenue', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}, {'name': 'avg_tip', 'type': 'double'}], 'example_questions': ['most popular routes', 'top pickup to dropoff zones']}, 'route_top_dropoff_zones': {'description': 'Route Top Dropoff Zones — auto-generated, update manually', 'tier': 'route', 'columns': [{'name': 'dropoff_zone', 'type': 'string'}, {'name': 'dropoff_borough', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}], 'example_questions': []}, 'route_top_pickup_zones': {'description': 'Route Top Pickup Zones — auto-generated, update manually', 'tier': 'route', 'columns': [{'name': 'pickup_zone', 'type': 'string'}, {'name': 'pickup_borough', 'type': 'string'}, {'name': 'trip_count', 'type': 'int64'}, {'name': 'revenue', 'type': 'double'}, {'name': 'avg_fare', 'type': 'double'}, {'name': 'avg_distance', 'type': 'double'}], 'example_questions': []}}


_SUPERVISOR_SYSTEM = """You are a table selection agent for NYC yellow cab trip analytics.

Dataset tiers:
- kpi: pre-aggregated monthly/weekly/daily metrics — prefer these for summary questions
- fact: daily/hourly grain with zone and vendor IDs — use for detailed filtering
- dim: lookup tables (zone names, boroughs, vendors)
- route: pickup-to-dropoff zone pair aggregates
- ops: operational patterns (peak hours, passenger counts, distances)
- dq: data quality checks — only if asked about data quality

Borough names: Manhattan, Brooklyn, Queens, Bronx, Staten Island.
Revenue = total_fare_amount (excludes tips). Peak hours = 7-9am and 5-8pm.

Select ONE table. Output ONLY valid JSON, no explanation:
{"table": "<table_name>", "confidence": "high|low", "reasoning": "<one sentence>"}"""


def _registry_as_prompt(registry: dict) -> str:
    lines = []
    for table, entry in registry.items():
        col_list = ", ".join(f"{c['name']}({c['type']})" for c in entry["columns"])
        examples = "; ".join(entry.get("example_questions", []))
        lines.append(
            f"- {table} [{entry['tier']}]: {entry['description']} | columns: {col_list} | examples: {examples}"
        )
    return "\n".join(lines)


def _run_supervisor(question: str, registry: dict, litellm_url: str = LITELLM_URL, litellm_model: str = LITELLM_MODEL, api_key: str = "") -> dict:
    """Returns {"table": str, "confidence": "high|low", "reasoning": str}."""
    registry_text = _registry_as_prompt(registry)
    messages = [
        {"role": "system", "content": _SUPERVISOR_SYSTEM},
        {"role": "user", "content": f"Available tables:\n{registry_text}\n\nQuestion: {question}"},
    ]
    raw = _llm_chat(messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key)
    cleaned = _strip_fences(raw)
    parsed = json.loads(cleaned.strip())
    table = parsed.get("table", "")
    if table not in registry:
        raise ValueError(f"Supervisor selected unknown table: {table}")
    confidence = parsed.get("confidence", "low")
    if confidence not in ("high", "low"):
        confidence = "low"
    return {"table": table, "confidence": confidence, "reasoning": parsed.get("reasoning", "")}


S3_BUCKET = "llmops-analytics-492372116094"
AWS_REGION = "ap-southeast-1"
ROW_CAP = 200
DUCKDB_TIMEOUT = 30

_QUERY_SYSTEM = """You are a SQL query agent for NYC yellow cab trip analytics stored in Parquet files on S3.
Rules:
- Write ONE SELECT statement only
- No markdown, no explanation, just the SQL
- Use only the table and columns provided
- Revenue = total_fare_amount (excludes tips)
- Borough values: Manhattan, Brooklyn, Queens, Bronx, Staten Island
- Peak hours: 7-9 and 17-20 (24h)
- Do not use read_parquet(), httpfs, or any file functions"""


def _run_query(question: str, table: str, registry: dict, s3_bucket: str, aws_region: str = AWS_REGION, litellm_url: str = LITELLM_URL, litellm_model: str = LITELLM_MODEL, api_key: str = "") -> dict:
    """Returns {"sql": str, "rows": list[dict], "capped": bool}."""
    schema = registry[table]
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", aws_region):
        raise ValueError(f"Invalid aws_region format: {aws_region!r}")
    col_text = ", ".join(f"{c['name']} ({c['type']})" for c in schema["columns"])
    messages = [
        {"role": "system", "content": _QUERY_SYSTEM},
        {"role": "user", "content": f"Table: {table}\nColumns: {col_text}\n\nQuestion: {question}"},
    ]
    raw = _llm_chat(messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key)
    sql = _strip_fences(raw).rstrip(";").strip()
    _validate_sql(sql, table, set(registry.keys()))

    # Check whether the *top-level* query already has a LIMIT. Scanning the full
    # SQL string would match LIMIT inside CTEs (e.g. WITH x AS (... LIMIT 500))
    # and skip the outer cap, letting DuckDB materialise unbounded rows.
    # Walk at depth=0 only to detect a true top-level LIMIT.
    _depth, _top_limit = 0, False
    for _tok in re.split(r'(\(|\))', sql):
        if _tok == '(':
            _depth += 1
        elif _tok == ')':
            _depth -= 1
        elif _depth == 0 and re.search(r'\bLIMIT\s+\d+', _tok, re.IGNORECASE):
            _top_limit = True
            break
    if not _top_limit:
        sql_capped = f"SELECT * FROM ({sql}) _q LIMIT {ROW_CAP + 1}"
    else:
        # Model already added a top-level LIMIT; 512MB DuckDB cap is the backstop.
        sql_capped = sql

    import duckdb

    def _execute():
        conn = duckdb.connect(config={"memory_limit": "512MB", "extension_directory": "/tmp/duckdb-extensions"})
        try:
            path = f"s3://{s3_bucket}/{table}/*.parquet"
            conn.execute("INSTALL httpfs; LOAD httpfs;")
            conn.execute(f"""
                CREATE OR REPLACE SECRET _s3 (
                    TYPE S3,
                    PROVIDER CREDENTIAL_CHAIN,
                    REGION '{aws_region}'
                )
            """)
            conn.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
            return conn.execute(sql_capped).fetchdf().to_dict(orient="records")
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_execute)
        try:
            rows = future.result(timeout=DUCKDB_TIMEOUT)
        except FuturesTimeoutError:
            raise TimeoutError(f"DuckDB query exceeded {DUCKDB_TIMEOUT}s")

    capped = len(rows) > ROW_CAP
    return {"sql": sql, "rows": rows[:ROW_CAP], "capped": capped}


_SUMMARIZE_SYSTEM = """You are a business analytics summarizer for NYC yellow cab trip data.
Given a question and query result rows, output ONLY valid JSON:
{
  "summary": "<2-4 sentence business summary>",
  "chart_spec": {"type": "bar|line|pie|table", "x": "<column>", "y": "<column>", "series": []}
}
Rules:
- summary must be 2-4 sentences, no bullet points
- chart x and y must be column names from the provided rows
- Revenue means total_fare_amount (excludes tips)
- No markdown, no explanation outside the JSON"""


def _run_summarize(question: str, rows: list[dict], capped: bool, litellm_url: str = LITELLM_URL, litellm_model: str = LITELLM_MODEL, api_key: str = "") -> dict:
    """Returns {"summary": str, "chart_spec": dict|None}."""
    rows_json = json.dumps(rows[:50], default=str)
    if capped:
        cap_note = f" NOTE: results were capped at {ROW_CAP} rows; showing first 50 to model."
    elif len(rows) > 50:
        cap_note = f" NOTE: showing first 50 of {len(rows)} rows to model."
    else:
        cap_note = ""
    messages = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {"role": "user", "content": f"Question: {question}{cap_note}\n\nRows:\n{rows_json}"},
    ]
    raw = _llm_chat(messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key)
    parsed = json.loads(_strip_fences(raw).strip())
    summary = parsed.get("summary", "").strip()
    chart_spec = parsed.get("chart_spec")

    # Validate chart_spec columns against actual row keys
    if chart_spec and rows:
        col_names = set(rows[0].keys())
        if (chart_spec.get("x") not in col_names or
                chart_spec.get("y") not in col_names or
                chart_spec.get("type") not in {"bar", "line", "pie", "table"}):
            chart_spec = None

    return {"summary": summary, "chart_spec": chart_spec}


class Pipe:
    class Valves(BaseModel):
        """Open WebUI admin-configurable settings for this pipe."""
        s3_bucket: str = S3_BUCKET
        aws_region: str = AWS_REGION
        litellm_url: str = LITELLM_URL
        litellm_model: str = LITELLM_MODEL
        litellm_api_key: str = ""
        enabled: bool = True

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(self, body: dict, __event_emitter__=None) -> str | StreamingResponse:
        """Route message to analytics pipeline or LiteLLM passthrough based on intent."""
        if not self.valves.enabled:
            try:
                return await _stream_llm(
                    body.get("messages", []),
                    self.valves.litellm_url,
                    self.valves.litellm_model,
                    self.valves.litellm_api_key,
                )
            except Exception as e:
                traceback.print_exc()
                return f"Chat service error: {e}"

        messages = body.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            try:
                return await _stream_llm(messages, self.valves.litellm_url, self.valves.litellm_model, self.valves.litellm_api_key)
            except Exception as e:
                traceback.print_exc()
                return f"Chat service error: {e}"

        question = user_messages[-1].get("content", "").strip()
        if not question:
            try:
                return await _stream_llm(messages, self.valves.litellm_url, self.valves.litellm_model, self.valves.litellm_api_key)
            except Exception as e:
                traceback.print_exc()
                return f"Chat service error: {e}"

        intent = classify_intent(question)

        if intent == INTENT_CHAT:
            try:
                return await _stream_llm(messages, self.valves.litellm_url, self.valves.litellm_model, self.valves.litellm_api_key)
            except Exception as e:
                traceback.print_exc()
                return f"Chat service error: {e}"

        if intent == INTENT_AMBIGUOUS:
            return (
                "That sounds data-related — do you want me to run an analytics "
                "query on the NYC taxi dataset? If so, please describe what you'd "
                "like to know (e.g. 'show monthly revenue trend' or 'top boroughs by trips')."
            )

        # INTENT_ANALYTICS
        try:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Analyzing", "done": False}})

            result = _run_analytics(
                question,
                self.valves.s3_bucket,
                self.valves.aws_region,
                self.valves.litellm_url,
                self.valves.litellm_model,
                self.valves.litellm_api_key,
            )

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Analyzing", "done": True}})

            return result
        except Exception as e:
            traceback.print_exc()
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Analyzing", "done": True}})
            return f"Analytics pipeline error: {e}"


def _run_analytics(question: str, s3_bucket: str, aws_region: str = AWS_REGION, litellm_url: str = LITELLM_URL, litellm_model: str = LITELLM_MODEL, api_key: str = "") -> str:
    """Run full supervisor → query → summarize pipeline, return formatted response."""
    supervisor = _run_supervisor(question, REGISTRY, litellm_url, litellm_model, api_key)

    if supervisor["confidence"] == "low":
        return (
            "I wasn't confident which data to use for that question. "
            f"Could you be more specific? ({supervisor['reasoning']})"
        )

    table = supervisor["table"]

    query_result = _run_query(question, table, REGISTRY, s3_bucket, aws_region, litellm_url, litellm_model, api_key)
    rows = query_result["rows"]
    capped = query_result["capped"]

    if not rows:
        return "No data found for that query."

    summarize_result = _run_summarize(question, rows, capped, litellm_url, litellm_model, api_key)
    summary = summarize_result["summary"]
    chart_spec = summarize_result["chart_spec"]

    parts = [summary]
    if chart_spec:
        html = build_html_artifact(chart_spec, rows)
        if html:
            parts.append(html)

    return "\n\n".join(parts)
