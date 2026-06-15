import json
from dataclasses import dataclass, field
from analytics_agent.ollama_client import chat

VALID_CHART_TYPES = {"bar", "line", "pie", "table"}

SYSTEM_PROMPT = """You are a business analytics summarizer for NYC yellow cab trip data.
Your audience is operations and business users. Write in plain English.

Given a question and query result rows, output ONLY valid JSON:
{
  "summary": "<2-4 sentence business summary>",
  "chart_spec": {"type": "bar|line|pie|table", "x": "<column>", "y": "<column>", "series": []},
  "capped": <true if rows were capped, else false>
}

Rules:
- summary must be 2-4 sentences, no bullet points
- chart x and y must be column names from the provided rows
- if rows were capped at 200, note it in the summary
- Revenue means total_fare_amount (excludes tips)
- No markdown, no explanation outside the JSON"""


@dataclass
class SummarizeResult:
    summary: str
    chart_spec: dict | None
    capped: bool
    chart_invalid_reason: str = ""
    raw_response: str = ""


class SummarizeError(Exception):
    pass


def _validate_chart_spec(chart_spec: dict, rows: list[dict]) -> str:
    if not rows:
        return "no rows to chart"
    if chart_spec.get("type") not in VALID_CHART_TYPES:
        return f"invalid chart type: {chart_spec.get('type')}"
    col_names = set(rows[0].keys())
    if chart_spec.get("x") not in col_names:
        return f"x column '{chart_spec.get('x')}' not in rows"
    if chart_spec.get("y") not in col_names:
        return f"y column '{chart_spec.get('y')}' not in rows"
    return ""


def run_summarize_agent(question: str, rows: list[dict], capped: bool) -> SummarizeResult:
    rows_json = json.dumps(rows[:50], default=str)
    cap_note = " NOTE: results were capped at 200 rows." if capped else ""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Question: {question}{cap_note}\n\nRows:\n{rows_json}",
        },
    ]
    raw = chat(messages=messages)

    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise SummarizeError(f"Failed to parse summarize output: {e} | raw: {raw}")

    summary = parsed.get("summary", "").strip()
    if not summary:
        raise SummarizeError("empty summary returned by model")

    chart_spec = parsed.get("chart_spec")
    chart_invalid = ""
    if chart_spec:
        chart_invalid = _validate_chart_spec(chart_spec, rows)
        if chart_invalid:
            chart_spec = None

    return SummarizeResult(
        summary=summary,
        chart_spec=chart_spec,
        capped=parsed.get("capped", capped),
        chart_invalid_reason=chart_invalid,
        raw_response=raw,
    )
