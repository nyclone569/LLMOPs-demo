"""
title: NYC Taxi Analytics Filter
author: llmops
version: 1.0.0
license: MIT
requirements: duckdb==1.2.2
"""

from __future__ import annotations

import httpx
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

    x_type = "temporal" if chart_type == "line" else "ordinal"

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
    stripped = sql.strip()
    if _FILE_FUNCTIONS.search(stripped):
        raise SQLValidationError("file function not allowed (read_parquet, httpfs, COPY, etc.)")
    if not stripped.upper().startswith("SELECT"):
        raise SQLValidationError("SQL must start with SELECT")
    if _DDL_KEYWORDS.search(stripped):
        raise SQLValidationError("DDL keywords not allowed")
    if ";" in stripped:
        raise SQLValidationError("chained statements not allowed")
    if expected_table not in known_tables:
        raise SQLValidationError(f"Table '{expected_table}' not in registry")
    found = set(re.findall(r"\bFROM\s+(\w+)", stripped, re.IGNORECASE))
    found |= set(re.findall(r"\bJOIN\s+(\w+)", stripped, re.IGNORECASE))
    for t in found:
        if t.lower() != expected_table.lower():
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


OLLAMA_URL = "http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions"
OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_TIMEOUT = 60


def _ollama_chat(messages: list[dict], model: str = OLLAMA_MODEL, ollama_url: str = OLLAMA_URL) -> str:
    """Direct HTTP call to Ollama OpenAI-compatible endpoint."""
    resp = httpx.post(
        ollama_url,
        json={"model": model, "messages": messages},
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
