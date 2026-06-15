"""
title: NYC Taxi Analytics Filter
author: llmops
version: 1.0.0
license: MIT
requirements: duckdb==1.2.2
"""

from __future__ import annotations

import json
import re

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

    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": mark,
        "data": {"values": rows},
        "encoding": {
            "x": {"field": x_field, "type": "ordinal", **x_orient},
            "y": {"field": y_field, "type": "quantitative"},
        },
        "width": "container",
        "height": 300,
    }


def build_html_artifact(chart_spec: dict, rows: list[dict]) -> str | None:
    """Wrap a Vega-Lite spec in a self-contained HTML artifact string.

    Returns None for table type (text-only result).
    """
    if chart_spec.get("type") == "table":
        return None

    vl_spec = chart_spec_to_vegalite(chart_spec, rows)
    spec_json = json.dumps(vl_spec)

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
