import json
from dataclasses import dataclass, field
from analytics_agent.ollama_client import chat, strip_fences
from analytics_agent.registry import registry_as_prompt_text

SYSTEM_PROMPT = """You are a table selection agent for NYC yellow cab trip analytics.

Dataset tiers:
- kpi: pre-aggregated monthly/weekly/daily metrics — prefer these for summary questions
- fact: daily/hourly grain with zone and vendor IDs — use for detailed filtering
- dim: lookup tables (zone names, boroughs, vendors)
- route: pickup-to-dropoff zone pair aggregates
- ops: operational patterns (peak hours, passenger counts, distances)
- dq: data quality checks — only if asked about data quality

Borough names in this dataset: Manhattan, Brooklyn, Queens, Bronx, Staten Island.
Revenue = total_fare_amount (excludes tips).
Peak hours = 7-9am and 5-8pm.

Select ONE table. Output ONLY valid JSON, no explanation, no markdown:
{"table": "<table_name>", "confidence": "high|low", "reasoning": "<one sentence>"}

You MUST output exactly "high" or "low" for confidence. No other values."""


@dataclass
class SupervisorResult:
    table: str
    confidence: str
    reasoning: str
    raw_response: str
    unexpected_confidence: str = ""


class SupervisorError(Exception):
    pass


def run_supervisor(question: str, registry: dict) -> SupervisorResult:
    registry_text = registry_as_prompt_text(registry)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Available tables:\n{registry_text}\n\nQuestion: {question}",
        },
    ]
    raw = chat(messages=messages)

    # Strip markdown fences if model wraps JSON in code blocks
    cleaned = strip_fences(raw)

    try:
        parsed = json.loads(cleaned.strip())
    except json.JSONDecodeError as e:
        raise SupervisorError(f"Failed to parse supervisor output: {e} | raw: {raw}")

    table = parsed.get("table", "")
    if table not in registry:
        raise SupervisorError(f"Supervisor selected '{table}' which is not in registry")

    confidence = parsed.get("confidence", "low")
    unexpected = ""
    if confidence not in ("high", "low"):
        unexpected = confidence
        confidence = "low"

    return SupervisorResult(
        table=table,
        confidence=confidence,
        reasoning=parsed.get("reasoning", ""),
        raw_response=raw,
        unexpected_confidence=unexpected,
    )
