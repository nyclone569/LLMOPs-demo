"""
title: NYC Taxi Analytics Pipe
author: llmops
version: 1.0.0
license: MIT
requirements: duckdb==1.2.2, httpx>=0.27, pydantic>=2
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pydantic import BaseModel
from starlette.responses import StreamingResponse
from typing import Optional
from pathlib import Path
import html as html_lib
import hashlib
import duckdb
import httpx
import json
import os
import sqlite3
import time
import uuid
import re
import traceback
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

DOMAIN_TERMS = {
    "taxi",
    "trip",
    "trips",
    "fare",
    "borough",
    "zone",
    "pickup",
    "dropoff",
    "vendor",
    "route",
    "revenue",
    "passenger",
    "passengers",
    "yellow",
    "green",
    "fhv",
    "manhattan",
    "brooklyn",
    "queens",
    "bronx",
    "staten island",
}

ANALYTICS_WORDS = {
    "how many",
    "average",
    "total",
    "compare",
    "top",
    "trend",
    "count",
    "per",
    "rate",
    "show",
    "summary",
    "breakdown",
    "most",
    "least",
    "peak",
    "weekly",
    "monthly",
    "daily",
    "hourly",
}

TABLE_PRESENTATION_WORDS = {
    "table",
    "rows",
    "row",
    "list",
    "tabular",
    "show data",
    "result table",
    "data table",
}

CHART_PRESENTATION_WORDS = {
    "chart",
    "graph",
    "plot",
    "visualize",
    "visualise",
    "trend line",
    "bar chart",
    "line chart",
}

TABLE_IDENTIFIER = re.compile(
    r"\b(?:kpi|fact|dim|route|ops|dq)[_-][a-z0-9][a-z0-9_-]*\b",
    re.IGNORECASE,
)

INTENT_ANALYTICS = "analytics"
INTENT_AMBIGUOUS = "ambiguous"
INTENT_CHAT = "chat"


def classify_intent(message: str) -> str:
    """Three-tier intent classification based on domain + analytics signal counts."""
    lower = message.lower()
    match_text = re.sub(r"[_-]+", " ", lower)

    analytics_count = sum(
        1 for word in ANALYTICS_WORDS if re.search(rf"\b{re.escape(word)}\b", match_text)
    )

    if TABLE_IDENTIFIER.search(lower):
        if analytics_count >= 1:
            return INTENT_ANALYTICS
        return INTENT_AMBIGUOUS

    domain_count = sum(
        1 for term in DOMAIN_TERMS if re.search(rf"\b{re.escape(term)}\b", match_text)
    )

    if domain_count >= 1 and analytics_count >= 1:
        return INTENT_ANALYTICS
    if domain_count >= 1:
        return INTENT_AMBIGUOUS
    return INTENT_CHAT


def _has_phrase(message: str, phrases: set[str]) -> bool:
    lower = message.lower()
    return any(re.search(rf"\b{re.escape(phrase)}\b", lower) for phrase in phrases)


def _select_presentation_mode(question: str, rows: list[dict]) -> str:
    """Return chart, table, both, text, or auto based on explicit display intent."""
    if not rows:
        return "text"

    wants_table = _has_phrase(question, TABLE_PRESENTATION_WORDS)
    wants_chart = _has_phrase(question, CHART_PRESENTATION_WORDS)

    if wants_table and wants_chart:
        return "both"
    if wants_table:
        return "table"
    if wants_chart:
        return "chart"
    return "auto"


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
        "height": 420,
        "autosize": {"type": "fit", "contains": "padding", "resize": True},
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


def _split_plan_and_sql(text: str) -> tuple[str, str]:
    match = re.search(r"^SQL:\s*", text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return "", text.strip()

    plan = text[: match.start()].strip()
    sql = text[match.end() :].strip()
    if plan.upper().startswith("PLAN:"):
        plan = plan[5:].strip()
    return plan, sql


def _retry_prompt(exc: Exception, table: str) -> str:
    return (
        f"Your SQL was rejected: {exc}. "
        "Re-read the GROUP BY rules and the columns list, fix the issue, "
        f"and rewrite as ONE SELECT against {table}. Return PLAN then SQL."
    )



def _normalize_duckdb_sql(sql: str) -> str:
    """Normalize common non-DuckDB date syntax produced by SQL LLMs."""
    return re.sub(
        r"\bDATE_SUB\s*\(\s*CURRENT_DATE\s*\(\s*\)\s*,\s*INTERVAL\s+(\d+)\s+DAY\s*\)",
        r"CURRENT_DATE - INTERVAL \1 DAY",
        sql,
        flags=re.IGNORECASE,
    )


def _validate_sql(sql: str, expected_table: str, known_tables: set) -> None:
    stripped = sql.strip().rstrip(";").strip()
    if _FILE_FUNCTIONS.search(stripped):
        raise SQLValidationError(
            "file function not allowed (read_parquet, httpfs, COPY, etc.)"
        )
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
    cte_names = {
        m.lower()
        for m in re.findall(r"\bWITH\s+(\w+)\s+AS\s*\(", stripped, re.IGNORECASE)
    }
    for t in found:
        if t.lower() != expected_table.lower() and t.lower() not in cte_names:
            raise SQLValidationError(
                f"Table '{t}' not allowed — expected '{expected_table}'"
            )


def _infer_table_columns(rows: list[dict]) -> list[dict]:
    """Infer stable table columns from returned row keys."""
    if not rows:
        return []

    keys: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(key)

    columns = []
    for key in keys:
        sample = next((row.get(key) for row in rows if row.get(key) is not None), None)
        if isinstance(sample, bool):
            col_type = "boolean"
        elif isinstance(sample, (int, float)):
            col_type = "number"
        else:
            col_type = "string"
        columns.append({"key": key, "label": key, "type": col_type})
    return columns


def build_table_artifact(rows: list[dict], metadata: dict | None = None) -> str:
    """Build a self-contained, view-only HTML table artifact."""
    metadata = metadata or {}
    columns = _infer_table_columns(rows)
    safe_rows_json = json.dumps(rows, default=str).replace("</", "<\\/")
    safe_columns_json = json.dumps(columns, default=str).replace("</", "<\\/")
    row_cap = int(metadata.get("row_cap", ROW_CAP))
    capped = bool(metadata.get("capped", False))
    data_as_of = metadata.get("data_as_of")
    capped_label = f"Showing first {row_cap} rows" if capped else ""
    data_as_of_label = f"Data as of {data_as_of}" if data_as_of else ""

    escaped_capped_label = html_lib.escape(capped_label)
    escaped_data_as_of_label = html_lib.escape(data_as_of_label)
    preview_rows = rows[:25]
    preview_cells = []
    for row in preview_rows:
        preview_cells.append("<tr>")
        for column in columns:
            css_class = ' class="number"' if column["type"] == "number" else ""
            cell_value = row.get(column["key"])
            value = html_lib.escape("" if cell_value is None else str(cell_value))
            preview_cells.append(f'<td{css_class} title="{value}">{value}</td>')
        preview_cells.append("</tr>")
    escaped_preview_rows = "".join(preview_cells)

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    :root {{
      color-scheme: light dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    body {{
      margin: 0;
      padding: 12px;
      background: transparent;
      color: #111827;
      font-size: 13px;
    }}
    .shell {{
      border: 1px solid #d1d5db;
      border-radius: 8px;
      overflow: hidden;
      background: #ffffff;
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-bottom: 1px solid #e5e7eb;
      background: #f9fafb;
      flex-wrap: wrap;
    }}
    .title {{
      font-weight: 650;
      color: #111827;
    }}
    .meta {{
      color: #4b5563;
      font-size: 12px;
    }}
    .controls {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}
    input, select, button {{
      border: 1px solid #d1d5db;
      border-radius: 6px;
      background: #ffffff;
      color: #111827;
      font: inherit;
      min-height: 30px;
    }}
    input {{
      padding: 4px 8px;
      width: 220px;
    }}
    select, button {{
      padding: 4px 8px;
    }}
    button:disabled {{
      opacity: 0.45;
      cursor: not-allowed;
    }}
    .table-wrap {{
      max-height: 520px;
      overflow: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 640px;
    }}
    th, td {{
      padding: 8px 10px;
      border-bottom: 1px solid #e5e7eb;
      text-align: left;
      max-width: 280px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #f3f4f6;
      z-index: 1;
      cursor: pointer;
      user-select: none;
      font-weight: 650;
    }}
    td.number {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    tbody tr:nth-child(even) {{
      background: #f9fafb;
    }}
    .footer {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-top: 1px solid #e5e7eb;
      background: #f9fafb;
      flex-wrap: wrap;
    }}
    .empty {{
      padding: 28px 12px;
      text-align: center;
      color: #6b7280;
    }}
    @media (prefers-color-scheme: dark) {{
      body {{ color: #e5e7eb; }}
      .shell {{ background: #111827; border-color: #374151; }}
      .toolbar, .footer, th {{ background: #1f2937; border-color: #374151; }}
      .title {{ color: #f9fafb; }}
      .meta {{ color: #d1d5db; }}
      input, select, button {{ background: #111827; color: #e5e7eb; border-color: #4b5563; }}
      th, td {{ border-color: #374151; }}
      tbody tr:nth-child(even) {{ background: #172033; }}
      .empty {{ color: #9ca3af; }}
    }}
  </style>
</head>
<body>
  <div class="shell" data-analytics-table>
    <div class="toolbar">
      <div>
        <div class="title">Query result table</div>
        <div class="meta" id="row-count"></div>
        <div class="meta">{escaped_capped_label}</div>
        <div class="meta">{escaped_data_as_of_label}</div>
      </div>
      <div class="controls">
        <input id="global-search" type="search" placeholder="Search results" autocomplete="off">
        <label class="meta" for="page-size">Rows</label>
        <select id="page-size">
          <option value="10">10</option>
          <option value="25" selected>25</option>
          <option value="50">50</option>
          <option value="100">100</option>
        </select>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead id="table-head"></thead>
        <tbody id="table-body">{escaped_preview_rows}</tbody>
      </table>
      <div class="empty" id="empty-state" hidden>No rows returned</div>
    </div>
    <div class="footer">
      <span class="meta" id="page-status"></span>
      <div class="controls">
        <button id="prev-page" type="button">Previous</button>
        <button id="next-page" type="button">Next</button>
      </div>
    </div>
  </div>
  <script type="application/json" id="table-data">{safe_rows_json}</script>
  <script type="application/json" id="table-columns">{safe_columns_json}</script>
  <script>
    const rows = JSON.parse(document.getElementById('table-data').textContent);
    const columns = JSON.parse(document.getElementById('table-columns').textContent);
    let filteredRows = rows.slice();
    let page = 1;
    let pageSize = 25;
    let sortState = {{ key: null, direction: 'asc' }};

    const head = document.getElementById('table-head');
    const body = document.getElementById('table-body');
    const emptyState = document.getElementById('empty-state');
    const rowCount = document.getElementById('row-count');
    const pageStatus = document.getElementById('page-status');
    const search = document.getElementById('global-search');
    const pageSizeSelect = document.getElementById('page-size');
    const prev = document.getElementById('prev-page');
    const next = document.getElementById('next-page');

    function escapeText(value) {{
      return String(value ?? '');
    }}

    function compareValues(a, b, type) {{
      if (type === 'number') {{
        const an = Number(a);
        const bn = Number(b);
        if (Number.isFinite(an) && Number.isFinite(bn)) return an - bn;
      }}
      return escapeText(a).localeCompare(escapeText(b), undefined, {{ numeric: true, sensitivity: 'base' }});
    }}

    function renderHead() {{
      const tr = document.createElement('tr');
      columns.forEach((column) => {{
        const th = document.createElement('th');
        th.textContent = column.label;
        th.title = 'Sort by ' + column.label;
        th.addEventListener('click', () => {{
          if (sortState.key === column.key) {{
            sortState.direction = sortState.direction === 'asc' ? 'desc' : 'asc';
          }} else {{
            sortState = {{ key: column.key, direction: 'asc' }};
          }}
          page = 1;
          render();
        }});
        tr.appendChild(th);
      }});
      head.replaceChildren(tr);
    }}

    function applyFilter() {{
      const query = search.value.trim().toLowerCase();
      filteredRows = rows.filter((row) => {{
        if (!query) return true;
        return columns.some((column) => escapeText(row[column.key]).toLowerCase().includes(query));
      }});
      if (sortState.key) {{
        const column = columns.find((candidate) => candidate.key === sortState.key) || {{ type: 'string' }};
        filteredRows.sort((a, b) => {{
          const result = compareValues(a[sortState.key], b[sortState.key], column.type);
          return sortState.direction === 'asc' ? result : -result;
        }});
      }}
    }}

    function renderBody() {{
      applyFilter();
      const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
      page = Math.min(page, totalPages);
      const start = (page - 1) * pageSize;
      const visible = filteredRows.slice(start, start + pageSize);

      body.replaceChildren();
      visible.forEach((row) => {{
        const tr = document.createElement('tr');
        columns.forEach((column) => {{
          const td = document.createElement('td');
          const value = escapeText(row[column.key]);
          td.textContent = value;
          td.title = value;
          if (column.type === 'number') td.classList.add('number');
          tr.appendChild(td);
        }});
        body.appendChild(tr);
      }});

      emptyState.hidden = filteredRows.length !== 0;
      rowCount.textContent = filteredRows.length === rows.length
        ? `Showing ${{visible.length ? start + 1 : 0}}-${{start + visible.length}} of ${{rows.length}} rows`
        : `Showing ${{visible.length ? start + 1 : 0}}-${{start + visible.length}} of ${{filteredRows.length}} filtered rows`;
      pageStatus.textContent = `Page ${{page}} of ${{totalPages}}`;
      prev.disabled = page <= 1;
      next.disabled = page >= totalPages;
    }}

    function render() {{
      renderBody();
      reportHeight();
    }}

    function reportHeight() {{
      parent.postMessage({{ type: 'iframe:height', height: document.documentElement.scrollHeight }}, '*');
    }}

    search.addEventListener('input', () => {{ page = 1; render(); }});
    pageSizeSelect.addEventListener('change', () => {{
      pageSize = Number(pageSizeSelect.value);
      page = 1;
      render();
    }});
    prev.addEventListener('click', () => {{ page -= 1; render(); }});
    next.addEventListener('click', () => {{ page += 1; render(); }});
    window.addEventListener('load', reportHeight);
    new ResizeObserver(reportHeight).observe(document.body);

    renderHead();
    render();
  </script>
</body>
</html>"""


def build_html_artifact(chart_spec: dict, rows: list[dict]) -> str | None:
    """Wrap a chart or table spec in a self-contained HTML artifact string."""
    if chart_spec.get("type") == "table":
        return build_table_artifact(rows, {"row_cap": ROW_CAP, "capped": False})

    vl_spec = chart_spec_to_vegalite(chart_spec, rows)
    spec_json = json.dumps(vl_spec, default=str)
    spec_json = spec_json.replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html>
<head>
  <script src="https://cdn.jsdelivr.net/npm/vega@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
  <style>
    html, body {{ margin: 0; padding: 0; width: 100%; }}
    #chart {{ width: 100%; min-height: 420px; }}
    .vega-embed, .vega-embed > canvas, .vega-embed > svg {{ max-width: 100%; }}
  </style>
</head>
<body>
  <div id="chart"></div>
  <script>
    function reportHeight() {{
      var h = document.documentElement.scrollHeight;
      parent.postMessage({{ type: 'iframe:height', height: h }}, '*');
    }}
    vegaEmbed('#chart', {spec_json}, {{actions: false, renderer: 'canvas'}}).then(function() {{
      reportHeight();
    }});
    new ResizeObserver(reportHeight).observe(document.body);
    window.addEventListener('load', reportHeight);
  </script>
</body>
</html>"""


def _webui_upload_dir() -> str:
    """Return Open WebUI's upload directory, with a local default for the pod."""
    try:
        from open_webui.config import UPLOAD_DIR

        return str(UPLOAD_DIR)
    except Exception:
        return os.getenv("UPLOAD_DIR", "/app/backend/data/uploads")


def _persist_html_artifact(
    html: str, db_path: str | None = None, upload_dir: str | None = None
) -> str:
    """Persist an HTML artifact where Open WebUI can render it as an iframe.

    Open WebUI's frontend turns <file type="html" id="..."> tokens into a
    sandboxed iframe served by /api/v1/files/{id}/content/html. That backend
    endpoint only serves admin-owned files, so the row must be linked to an
    admin user.
    """
    db_path = db_path or os.getenv("WEBUI_DB_PATH", "/app/backend/data/webui.db")
    upload_root = Path(upload_dir or _webui_upload_dir())
    file_id = str(uuid.uuid4())
    filename = f"nyc_taxi_chart_{file_id}.html"
    file_path = upload_root / f"{file_id}_{filename}"
    html_bytes = html.encode("utf-8")
    now = int(time.time())

    conn = sqlite3.connect(db_path)
    try:
        admin = conn.execute(
            "SELECT id FROM user WHERE role = 'admin' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not admin:
            raise RuntimeError(
                "Open WebUI HTML chart rendering requires an admin user row"
            )

        upload_root.mkdir(parents=True, exist_ok=True)
        file_path.write_text(html, encoding="utf-8")

        conn.execute(
            """
            INSERT INTO file (id, user_id, filename, meta, created_at, hash, data, updated_at, path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                admin[0],
                filename,
                json.dumps(
                    {
                        "name": filename,
                        "content_type": "text/html",
                        "size": len(html_bytes),
                    }
                ),
                now,
                hashlib.sha256(html_bytes).hexdigest(),
                json.dumps({}),
                now,
                str(file_path),
            ),
        )
        conn.commit()
    except Exception:
        try:
            if file_path.exists():
                file_path.unlink()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    return f'<file type="html" id="{file_id}">'


LITELLM_URL = "http://litellm.litellm.svc.cluster.local:4000/v1/chat/completions"
LITELLM_MODEL = "private-chat"
LITELLM_TIMEOUT = 60


def _llm_chat(
    messages: list[dict],
    model: str = LITELLM_MODEL,
    litellm_url: str = LITELLM_URL,
    api_key: str = "",
) -> str:
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


async def _stream_llm(
    messages: list[dict],
    litellm_url: str = LITELLM_URL,
    model: str = LITELLM_MODEL,
    api_key: str = "",
) -> StreamingResponse:
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


# ---------------------------------------------------------------------------
# Schema registry — S3-backed with TTL cache
# ---------------------------------------------------------------------------

_registry_cache: dict | None = None
_registry_ts: float = 0.0


def _fetch_registry_from_s3(s3_bucket: str, aws_region: str) -> dict:
    """Fetch schema_registry.json from S3 using IRSA web identity or credential chain.

    Reuses the same STS web identity exchange pattern as _create_s3_secret so
    no additional credentials are needed in the pod.
    """
    role_arn = os.getenv("AWS_ROLE_ARN")
    token_file = os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE")

    headers: dict = {}
    if role_arn and token_file:
        with open(token_file, "r", encoding="utf-8") as f:
            web_identity_token = f.read()
        body = urllib.parse.urlencode(
            {
                "Action": "AssumeRoleWithWebIdentity",
                "Version": "2011-06-15",
                "RoleArn": role_arn,
                "RoleSessionName": "openwebui-registry-fetch",
                "WebIdentityToken": web_identity_token,
            }
        ).encode()
        req = urllib.request.Request(
            f"https://sts.{aws_region}.amazonaws.com/",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        root = ET.fromstring(resp.read())
        ns = {"sts": "https://sts.amazonaws.com/doc/2011-06-15/"}
        access_key = root.findtext(".//sts:Credentials/sts:AccessKeyId", namespaces=ns)
        secret_key = root.findtext(".//sts:Credentials/sts:SecretAccessKey", namespaces=ns)
        session_token = root.findtext(".//sts:Credentials/sts:SessionToken", namespaces=ns)
        if not access_key or not secret_key or not session_token:
            raise RuntimeError("STS AssumeRoleWithWebIdentity did not return complete credentials")

        # Build a minimal AWS Signature V4 signed request for S3 GET.
        # For simplicity in a controlled EKS environment, presign via query params
        # is complex; instead use the temporary credentials with httpx (already a dep).
        headers = {
            "x-amz-security-token": session_token,
        }
        import hmac
        import hashlib as _hl
        import datetime

        now = datetime.datetime.utcnow()
        datestamp = now.strftime("%Y%m%d")
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        method = "GET"
        canonical_uri = "/schema_registry.json"
        canonical_querystring = ""
        host = f"{s3_bucket}.s3.{aws_region}.amazonaws.com"
        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n"
            f"x-amz-date:{amzdate}\n"
            f"x-amz-security-token:{session_token}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date;x-amz-security-token"
        payload_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        canonical_request = "\n".join([
            method, canonical_uri, canonical_querystring,
            canonical_headers, signed_headers, payload_hash,
        ])

        credential_scope = f"{datestamp}/{aws_region}/s3/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amzdate, credential_scope,
            _hl.sha256(canonical_request.encode()).hexdigest(),
        ])

        def _sign(key, msg):
            return hmac.new(key, msg.encode(), _hl.sha256).digest()

        signing_key = _sign(
            _sign(
                _sign(
                    _sign(f"AWS4{secret_key}".encode(), datestamp),
                    aws_region,
                ),
                "s3",
            ),
            "aws4_request",
        )
        signature = hmac.new(signing_key, string_to_sign.encode(), _hl.sha256).hexdigest()
        auth_header = (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        url = f"https://{host}{canonical_uri}"
        req2 = urllib.request.Request(url, headers={
            "Authorization": auth_header,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amzdate,
            "x-amz-security-token": session_token,
        })
        resp2 = urllib.request.urlopen(req2, timeout=10)
        return json.loads(resp2.read().decode("utf-8"))

    # No IRSA — fall back to unsigned request (works with IAM instance profile /
    # credential chain if the bucket policy allows).
    url = f"https://{s3_bucket}.s3.{aws_region}.amazonaws.com/schema_registry.json"
    req3 = urllib.request.Request(url)
    resp3 = urllib.request.urlopen(req3, timeout=10)
    return json.loads(resp3.read().decode("utf-8"))


def _load_registry(s3_bucket: str, aws_region: str, ttl: int = 300) -> dict:
    """Return the schema registry, fetching from S3 when the TTL has expired.

    Falls back to stale cache on S3 errors. Raises on first-call failure.
    """
    global _registry_cache, _registry_ts

    now = time.time()
    if _registry_cache is not None and (now - _registry_ts) < ttl:
        return _registry_cache

    try:
        data = _fetch_registry_from_s3(s3_bucket, aws_region)
        _registry_cache = data
        _registry_ts = now
        return _registry_cache
    except Exception:
        if _registry_cache is not None:
            # Return stale data rather than blowing up
            return _registry_cache
        raise




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


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _format_prompt_list(
    label: str,
    values: list[str],
    none_label: str | None = None,
    separator: str = "; ",
) -> str | None:
    if values:
        return f"{label}: " + separator.join(values)
    if none_label is not None:
        return f"{label}: {none_label}"
    return None


def _registry_as_prompt(registry: dict) -> str:
    lines = []
    for table, entry in registry.items():
        col_list = ", ".join(f"{c['name']}({c['type']})" for c in entry["columns"])
        parts = [
            f"- {table} [{entry['tier']}]: {entry['description']}",
        ]

        metadata_parts = [
            _format_prompt_list("aliases", _as_list(entry.get("aliases"))),
            f"grain: {entry['grain']}" if entry.get("grain") else None,
            _format_prompt_list("dimensions", _as_list(entry.get("dimensions")), separator=", "),
            _format_prompt_list("measures", _as_list(entry.get("measures")), separator=", "),
            _format_prompt_list("date_columns", _as_list(entry.get("date_columns")), none_label="none")
            if "date_columns" in entry
            else None,
            _format_prompt_list("use_for", _as_list(entry.get("use_for"))),
            _format_prompt_list("avoid_for", _as_list(entry.get("avoid_for"))),
            _format_prompt_list("examples", _as_list(entry.get("example_questions"))),
            f"columns: {col_list}",
        ]
        parts.extend(part for part in metadata_parts if part)
        lines.append(" | ".join(parts))
    return "\n".join(lines)


EXACT_CANDIDATE_SCORE = 1000


def _normalize_match_text(value: str) -> str:
    """Normalize natural-language and schema labels for lexical matching."""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _compact_match_text(value: str) -> str:
    """Normalize text so spaces, hyphens, and underscores compare equally."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _entry_match_texts(table: str, entry: dict) -> dict[str, list[str]]:
    columns = [column.get("name", "") for column in entry.get("columns", [])]
    return {
        "table_words": _normalize_match_text(table).split(),
        "aliases": _as_list(entry.get("aliases")),
        "columns": columns,
        "dimensions": _as_list(entry.get("dimensions")),
        "measures": _as_list(entry.get("measures")),
        "use_for": _as_list(entry.get("use_for")),
        "examples": _as_list(entry.get("example_questions")),
        "description": _as_list(entry.get("description")),
    }


def _score_candidate(question: str, table: str, entry: dict) -> tuple[int, list[str]]:
    question_norm = _normalize_match_text(question)
    question_words = set(question_norm.split())
    texts = _entry_match_texts(table, entry)
    score = 0
    reasons: list[str] = []

    table_word_hits = [word for word in texts["table_words"] if word in question_words]
    if table_word_hits:
        score += 20 * len(table_word_hits)
        reasons.append("table words matched: " + ", ".join(table_word_hits))

    for field, weight in (
        ("aliases", 80),
        ("columns", 25),
        ("dimensions", 25),
        ("measures", 30),
        ("use_for", 35),
        ("examples", 25),
        ("description", 10),
    ):
        for text in texts[field]:
            text_norm = _normalize_match_text(text)
            if not text_norm:
                continue
            text_words = set(text_norm.split())
            overlap = sorted(question_words & text_words)
            if text_norm in question_norm:
                score += weight
                reasons.append(f"{field} phrase matched: {text}")
            elif overlap:
                score += min(weight, 8 * len(overlap))
                reasons.append(f"{field} words matched: {', '.join(overlap)}")

    return score, reasons


def _select_table_candidates(question: str, registry: dict, limit: int = 8) -> list[dict]:
    """Return likely table candidates before calling the LLM supervisor."""
    question_compact = _compact_match_text(question)
    exact_matches = []

    for table, entry in registry.items():
        table_label = table.replace("_", " ")
        if _compact_match_text(table_label) in question_compact:
            exact_matches.append({
                "table": table,
                "score": EXACT_CANDIDATE_SCORE,
                "match_type": "exact_table_name",
                "reasons": ["normalized table name matched"],
            })
            continue

        for alias in _as_list(entry.get("aliases")):
            alias_compact = _compact_match_text(alias)
            if alias_compact and alias_compact in question_compact:
                exact_matches.append({
                    "table": table,
                    "score": EXACT_CANDIDATE_SCORE - 10,
                    "match_type": "exact_alias",
                    "reasons": [f"alias matched: {alias}"],
                })
                break

    if exact_matches:
        return sorted(exact_matches, key=lambda item: item["score"], reverse=True)[:limit]

    scored = []
    for table, entry in registry.items():
        score, reasons = _score_candidate(question, table, entry)
        if score > 0:
            scored.append({
                "table": table,
                "score": score,
                "match_type": "lexical_score",
                "reasons": reasons[:5],
            })

    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]


def _candidate_registry(registry: dict, candidates: list[dict]) -> dict:
    return {
        candidate["table"]: registry[candidate["table"]]
        for candidate in candidates
        if candidate.get("table") in registry
    }


def _supervisor_from_exact_candidate(candidate: dict) -> dict:
    reason = "; ".join(candidate.get("reasons", [])) or "exact table match"
    return {
        "table": candidate["table"],
        "confidence": "high",
        "reasoning": reason,
    }


def _run_supervisor(
    question: str,
    registry: dict,
    litellm_url: str = LITELLM_URL,
    litellm_model: str = LITELLM_MODEL,
    api_key: str = "",
) -> dict:
    """Returns {"table": str, "confidence": "high|low", "reasoning": str}."""
    registry_text = _registry_as_prompt(registry)
    messages = [
        {"role": "system", "content": _SUPERVISOR_SYSTEM},
        {
            "role": "user",
            "content": f"Available tables:\n{registry_text}\n\nQuestion: {question}",
        },
    ]
    raw = _llm_chat(
        messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key
    )
    cleaned = _strip_fences(raw)
    parsed = json.loads(cleaned.strip())
    table = parsed.get("table", "")
    if table not in registry:
        return {
            "table": "",
            "confidence": "low",
            "reasoning": (
                "Requested data did not match an available table; selected table is not listed in the registry: "
                f"{table or 'unknown'}"
            ),
        }
    confidence = parsed.get("confidence", "low")
    if confidence not in ("high", "low"):
        confidence = "low"
    return {
        "table": table,
        "confidence": confidence,
        "reasoning": parsed.get("reasoning", ""),
    }


S3_BUCKET = "llmops-analytics-492372116094"
AWS_REGION = "ap-southeast-1"
ROW_CAP = 200
DUCKDB_TIMEOUT = 30


def _wrap_with_limit(sql: str, row_cap: int = ROW_CAP) -> tuple[str, bool]:
    depth, top_limit = 0, False
    for token in re.split(r"(\(|\))", sql):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0 and re.search(r"\bLIMIT\s+\d+", token, re.IGNORECASE):
            top_limit = True
            break

    if top_limit:
        return sql, False
    return f"SELECT * FROM ({sql}) _q LIMIT {row_cap + 1}", True

def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _create_s3_secret(conn, aws_region: str) -> str:
    """Create DuckDB S3 credentials, preferring EKS IRSA web identity when present.

    DuckDB 1.2.2 accepts PROVIDER CREDENTIAL_CHAIN, but in Open WebUI it does
    not resolve AWS_WEB_IDENTITY_TOKEN_FILE into usable S3 credentials. The AWS
    SDK STS flow works in the same pod, so exchange the token explicitly and pass
    the temporary credentials to DuckDB without logging them.
    """
    role_arn = os.getenv("AWS_ROLE_ARN")
    token_file = os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE")
    if role_arn and token_file:
        with open(token_file, "r", encoding="utf-8") as f:
            web_identity_token = f.read()

        body = urllib.parse.urlencode(
            {
                "Action": "AssumeRoleWithWebIdentity",
                "Version": "2011-06-15",
                "RoleArn": role_arn,
                "RoleSessionName": "openwebui-duckdb-analytics",
                "WebIdentityToken": web_identity_token,
            }
        ).encode()
        req = urllib.request.Request(
            f"https://sts.{aws_region}.amazonaws.com/",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        root = ET.fromstring(resp.read())
        ns = {"sts": "https://sts.amazonaws.com/doc/2011-06-15/"}
        access_key = root.findtext(".//sts:Credentials/sts:AccessKeyId", namespaces=ns)
        secret_key = root.findtext(
            ".//sts:Credentials/sts:SecretAccessKey", namespaces=ns
        )
        session_token = root.findtext(
            ".//sts:Credentials/sts:SessionToken", namespaces=ns
        )
        if not access_key or not secret_key or not session_token:
            raise RuntimeError(
                "STS AssumeRoleWithWebIdentity did not return complete credentials"
            )

        conn.execute(f"""
            CREATE OR REPLACE SECRET _s3 (
                TYPE S3,
                PROVIDER CONFIG,
                KEY_ID {_sql_literal(access_key)},
                SECRET {_sql_literal(secret_key)},
                SESSION_TOKEN {_sql_literal(session_token)},
                REGION {_sql_literal(aws_region)}
            )
        """)
        return "web_identity"

    conn.execute(f"""
        CREATE OR REPLACE SECRET _s3 (
            TYPE S3,
            PROVIDER CREDENTIAL_CHAIN,
            REGION {_sql_literal(aws_region)}
        )
    """)
    return "credential_chain"


def _execute_sql(conn, sql_capped: str) -> list[dict]:
    return conn.execute(sql_capped).fetchdf().to_dict(orient="records")


def _build_duckdb_conn(table: str, s3_bucket: str, aws_region: str):
    conn = duckdb.connect(
        config={
            "memory_limit": "512MB",
            "extension_directory": "/tmp/duckdb-extensions",
        }
    )
    path = f"s3://{s3_bucket}/{table}/*.parquet"
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    auth_mode = _create_s3_secret(conn, aws_region)
    print(f"DuckDB S3 auth mode: {auth_mode}; path: {path}")
    conn.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
    return conn


_QUERY_SYSTEM = """You are a SQL query agent for NYC yellow cab trip analytics on DuckDB reading Parquet files on S3.

OUTPUT CONTRACT
First, write a short PLAN paragraph (2-4 lines) covering:
- which columns from the table answer the question
- the grain you are answering at (row-level vs aggregated)
- any aggregation/GROUP BY you intend to use
- if the question conflicts (e.g. asks for two grains), which one you chose and why
Then, on a new line, write "SQL:" followed by ONE SELECT statement. No markdown fences.

GROUP BY RULES
- Every non-aggregated column in SELECT must appear in GROUP BY.
- If a column is already a measure on a pre-aggregated table (revenue, trip_count, avg_fare etc. on kpi_*/route_*/ops_*), do NOT re-aggregate unless rolling up to a coarser grain.
- When rolling up: SUM measures, AVG only ratios with care, COUNT(*) for trip_count rollups.

DUCKDB DIALECT
- Recent windows: CURRENT_DATE - INTERVAL 7 DAY (not DATE_SUB)
- Date parts: EXTRACT(month FROM date_col)
- No read_parquet(), httpfs, COPY, or file functions
- One SELECT statement, no semicolons, no DDL

DOMAIN
- Borough names: Manhattan, Brooklyn, Queens, Bronx, Staten Island
- Peak hours: 7-9 and 17-20 (24h)
- The revenue column is called `revenue` on most tables (16 tables) and `total_revenue` on a few (`fact_trips_daily`, `fact_trips_hourly_zone`, `kpi_monthly_summary`, `dq_*`). Use the exact name shown in the per-query Columns list.
- Pre-aggregated tables (`kpi_*`/`route_*`/`ops_*`/`fact_trips_borough`) already contain summed measures — select directly, do not re-aggregate unless rolling up to a coarser grain.

EXAMPLES

Q: top 20 pickup zones by total revenue, with a borough breakdown chart
Table: route_top_pickup_zones
PLAN: route_top_pickup_zones is pre-aggregated at zone grain. The user asked for top 20 zones AND a borough chart — conflicting grain. I'll answer at zone grain (more specific) and keep pickup_borough so the chart agent can group it downstream.
SQL:
SELECT pickup_zone, pickup_borough, revenue
FROM route_top_pickup_zones
ORDER BY revenue DESC
LIMIT 20

Q: weekly revenue trend over the last 8 weeks
Table: fact_trips_daily
PLAN: fact_trips_daily is at day grain. Need to roll up to weeks and aggregate revenue. Use DATE_TRUNC for the week bucket and a recent window filter.
SQL:
SELECT DATE_TRUNC('week', pickup_date) AS week,
       SUM(total_revenue) AS revenue
FROM fact_trips_daily
WHERE pickup_date >= CURRENT_DATE - INTERVAL 56 DAY
GROUP BY 1
ORDER BY 1"""


def _run_query(
    question: str,
    table: str,
    registry: dict,
    s3_bucket: str,
    aws_region: str = AWS_REGION,
    litellm_url: str = LITELLM_URL,
    litellm_model: str = LITELLM_MODEL,
    api_key: str = "",
) -> dict:
    """Returns {"sql": str, "plan": str, "rows": list[dict], "capped": bool}."""
    schema = registry[table]
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", aws_region):
        raise ValueError(f"Invalid aws_region format: {aws_region!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]", s3_bucket):
        raise ValueError(f"Invalid s3_bucket: {s3_bucket!r}")
    col_text = ", ".join(f"{c['name']} ({c['type']})" for c in schema["columns"])
    messages: list[dict] = [
        {"role": "system", "content": _QUERY_SYSTEM},
        {
            "role": "user",
            "content": f"Table: {table}\nColumns: {col_text}\n\nQuestion: {question}",
        },
    ]

    conn = _build_duckdb_conn(table, s3_bucket, aws_region)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            for attempt in range(2):
                raw = _llm_chat(
                    messages,
                    model=litellm_model,
                    litellm_url=litellm_url,
                    api_key=api_key,
                )
                stripped = _strip_fences(raw)
                plan, sql = _split_plan_and_sql(stripped)
                sql = _normalize_duckdb_sql(sql.rstrip(";").strip())

                try:
                    _validate_sql(sql, table, set(registry.keys()))
                    sql_capped, _ = _wrap_with_limit(sql)
                    future = executor.submit(_execute_sql, conn, sql_capped)
                    try:
                        rows = future.result(timeout=DUCKDB_TIMEOUT)
                    except FuturesTimeoutError:
                        raise TimeoutError(f"DuckDB query exceeded {DUCKDB_TIMEOUT}s")
                    capped = len(rows) > ROW_CAP
                    return {
                        "sql": sql,
                        "plan": plan,
                        "rows": rows[:ROW_CAP],
                        "capped": capped,
                    }
                except (SQLValidationError, duckdb.Error) as exc:
                    if attempt == 1:
                        raise
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": _retry_prompt(exc, table)})
    finally:
        conn.close()

    raise RuntimeError("SQL generation failed without returning a result")



_CHART_SPEC_SYSTEM = """You are a chart type selector for NYC yellow cab trip analytics.
Given a question and column names from the query result, output ONLY valid JSON:
{"chart_spec": {"type": "bar|line|pie|table", "x": "<column>", "y": "<column>"}}
Rules:
- type must be one of: bar, line, pie, table
- x and y must be column names from the provided list
- Use "line" for time series or trends
- Use "bar" for comparisons across categories
- Use "pie" for market share or proportions (renders as sorted bar)
- Use "table" when no chart makes sense
- No markdown, no explanation outside the JSON"""


def _run_chart_spec(
    question: str,
    rows: list[dict],
    litellm_url: str = LITELLM_URL,
    litellm_model: str = LITELLM_MODEL,
    api_key: str = "",
) -> dict | None:
    """Returns chart_spec dict or None if invalid/error."""
    if not rows:
        return None
    col_names = list(rows[0].keys())
    messages = [
        {"role": "system", "content": _CHART_SPEC_SYSTEM},
        {"role": "user", "content": f"Question: {question}\nColumns: {', '.join(col_names)}"},
    ]
    try:
        raw = _llm_chat(messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key)
        parsed = json.loads(_strip_fences(raw).strip())
        chart_spec = parsed.get("chart_spec")
        if not chart_spec:
            return None
        col_set = set(col_names)
        if (
            chart_spec.get("x") not in col_set
            or chart_spec.get("y") not in col_set
            or chart_spec.get("type") not in {"bar", "line", "pie", "table"}
        ):
            return None
        return chart_spec
    except Exception:
        return None


_SUMMARY_STREAM_SYSTEM = """You are a business analytics summarizer for NYC yellow cab trip data.
Given a question and query result rows, write a 2-4 sentence business summary.
Rules:
- Plain text only, no JSON, no markdown, no bullet points
- Revenue means total_fare_amount (excludes tips)
- Be specific with numbers from the data
- No preamble like "Based on the data" — start directly with the insight"""


async def _stream_summary(
    question: str,
    rows: list[dict],
    capped: bool,
    litellm_url: str = LITELLM_URL,
    litellm_model: str = LITELLM_MODEL,
    api_key: str = "",
):
    """Async generator yielding summary tokens from LiteLLM streaming response.

    Attempts streaming first; falls back to synchronous call if streaming fails.
    """
    rows_json = json.dumps(rows[:50], default=str)
    if capped:
        cap_note = f" NOTE: results were capped at {ROW_CAP} rows; showing first 50."
    elif len(rows) > 50:
        cap_note = f" NOTE: showing first 50 of {len(rows)} rows."
    else:
        cap_note = ""
    messages = [
        {"role": "system", "content": _SUMMARY_STREAM_SYSTEM},
        {"role": "user", "content": f"Question: {question}{cap_note}\n\nRows:\n{rows_json}"},
    ]
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                litellm_url,
                json={"model": litellm_model, "messages": messages, "stream": True},
                headers=headers,
                timeout=httpx.Timeout(LITELLM_TIMEOUT, connect=10.0),
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise RuntimeError(
                        f"LiteLLM returned {response.status_code}: {body.decode('utf-8', errors='replace')[:200]}"
                    )
                buffer = ""
                async for chunk in response.aiter_bytes():
                    buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            return
                        try:
                            parsed = json.loads(data)
                            delta = parsed["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
    except Exception as stream_err:
        print(f"[analytics-pipe] Streaming summary failed: {stream_err}, falling back to sync")
        traceback.print_exc()
        result = _llm_chat(messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key)
        yield result


async def _stream_analytics(
    question: str,
    s3_bucket: str,
    aws_region: str,
    litellm_url: str,
    litellm_model: str,
    api_key: str,
    registry_ttl: int,
    duckdb_timeout: int,
    row_cap: int,
    emitter,
):
    """Async generator: yields markdown chunks for the analytics reasoning trace + summary."""
    try:
        registry = _load_registry(s3_bucket, aws_region, registry_ttl)
    except Exception as e:
        yield f"> **Error:** Could not load schema registry — {e}\n"
        return

    if emitter:
        await emitter({"type": "status", "data": {"description": "Selecting table from registry...", "done": False}})

    candidates: list[dict] = []
    try:
        candidates = _select_table_candidates(question, registry)
        exact_candidates = [
            candidate
            for candidate in candidates
            if candidate.get("match_type") in {"exact_table_name", "exact_alias"}
        ]
        if len(exact_candidates) == 1:
            supervisor = _supervisor_from_exact_candidate(exact_candidates[0])
        else:
            prompt_candidates = exact_candidates or candidates
            prompt_registry = _candidate_registry(registry, prompt_candidates) if prompt_candidates else registry
            supervisor = await asyncio.to_thread(
                _run_supervisor, question, prompt_registry, litellm_url, litellm_model, api_key,
            )
    except Exception as e:
        yield f"> **Error:** Table selection failed — {e}\n"
        if emitter:
            await emitter({"type": "status", "data": {"description": "Done", "done": True}})
        return

    table = supervisor["table"]
    confidence = supervisor["confidence"]
    reasoning = supervisor["reasoning"]

    if emitter:
        await emitter({"type": "status", "data": {"description": f"Selected `{table}` — generating SQL...", "done": False}})

    yield f"> **Table:** `{table}` — {reasoning} (confidence: {confidence})\n"

    if confidence == "low":
        suggestions = [c["table"] for c in candidates[:3] if c.get("table") and c["table"] != table]
        if suggestions:
            bullets = "\n".join(f"> - `{name}`" for name in suggestions)
            yield (
                "\nI wasn't confident which data to use. Did you mean one of these?\n"
                f"{bullets}\n"
                "\nIf so, ask again naming the table — otherwise please rephrase.\n"
            )
        else:
            yield "\nI wasn't confident which data to use. Could you be more specific?\n"
        if emitter:
            await emitter({"type": "status", "data": {"description": "Done", "done": True}})
        return

    try:
        t0 = time.time()
        query_result = await asyncio.to_thread(
            _run_query,
            question, table, registry, s3_bucket, aws_region,
            litellm_url, litellm_model, api_key,
        )
        elapsed = time.time() - t0
    except Exception as e:
        yield f"> **Error:** {e}\n"
        if emitter:
            await emitter({"type": "status", "data": {"description": "Done", "done": True}})
        return

    rows = query_result["rows"]
    sql = query_result["sql"]
    capped = query_result["capped"]
    plan = (query_result.get("plan") or "").strip()

    if plan:
        yield f"> **Plan:** {plan}\n"
    yield f"> **SQL:**\n> ```sql\n> {sql}\n> ```\n"
    yield f"> **Result:** {len(rows)} rows ({elapsed:.1f}s)\n\n"

    if not rows:
        yield "No data found for that query.\n"
        if emitter:
            await emitter({"type": "status", "data": {"description": "Done", "done": True}})
        return

    mode = _select_presentation_mode(question, rows)

    # Chart spec is an LLM round-trip that only depends on `rows`. Kick it off
    # now so it runs in parallel with the streaming summary instead of after.
    chart_task: asyncio.Task | None = None
    if mode in {"chart", "both", "auto"}:
        chart_task = asyncio.create_task(
            asyncio.to_thread(
                _run_chart_spec, question, rows, litellm_url, litellm_model, api_key,
            )
        )

    if emitter:
        await emitter({"type": "status", "data": {"description": f"Writing summary for {len(rows)} rows...", "done": False}})
    yield "---\n\n"
    try:
        async for token in _stream_summary(question, rows, capped, litellm_url, litellm_model, api_key):
            yield token
    except Exception as e:
        traceback.print_exc()
        yield f"\n\n> **Error:** Could not generate summary — {e}\n"

    artifacts: list[str] = []
    artifact_note = ""

    try:
        chart_spec = await chart_task if chart_task else None

        if mode == "auto" and chart_spec and chart_spec.get("type") == "table":
            mode = "table"
        elif mode == "auto" and chart_spec:
            mode = "chart"
        elif mode == "auto":
            mode = "table"

        if mode in {"chart", "both"}:
            chart_html = build_html_artifact(chart_spec, rows) if chart_spec else None
            if chart_html:
                artifacts.append(chart_html)
            elif mode == "chart":
                table_html = build_table_artifact(rows, {"row_cap": row_cap, "capped": capped})
                artifacts.append(table_html)

        if mode in {"table", "both"}:
            table_html = build_table_artifact(rows, {"row_cap": row_cap, "capped": capped})
            artifacts.append(table_html)
    except Exception:
        traceback.print_exc()
        artifact_note = "\n\n> **Note:** The requested table or chart could not be rendered.\n"

    if artifact_note:
        yield artifact_note

    if artifacts and emitter:
        await emitter({"type": "embeds", "data": {"embeds": artifacts}})

    yield "\n"
    if emitter:
        await emitter({"type": "status", "data": {"description": "Done", "done": True}})


class Pipe:
    class Valves(BaseModel):
        """Open WebUI admin-configurable settings for this pipe."""

        s3_bucket: str = S3_BUCKET
        aws_region: str = AWS_REGION
        litellm_url: str = LITELLM_URL
        litellm_model: str = LITELLM_MODEL
        litellm_api_key: str = ""
        enabled: bool = True
        registry_ttl: int = 300
        duckdb_timeout: int = DUCKDB_TIMEOUT
        row_cap: int = ROW_CAP

    def __init__(self):
        self.valves = self.Valves()

    async def pipe(self, body: dict, __event_emitter__=None):
        """Route message to analytics pipeline or LiteLLM passthrough based on intent.

        Returns:
            - StreamingResponse for chat / passthrough (Open WebUI streams it as SSE).
            - str for ambiguous clarifications.
            - AsyncGenerator[str] for analytics, so the reasoning trace and summary
              tokens reach the user as they're produced rather than after the whole
              pipeline (table → SQL → DuckDB → chart → summary) finishes.
        """
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
                return await _stream_llm(
                    messages,
                    self.valves.litellm_url,
                    self.valves.litellm_model,
                    self.valves.litellm_api_key,
                )
            except Exception as e:
                traceback.print_exc()
                return f"Chat service error: {e}"

        question = user_messages[-1].get("content", "").strip()
        if not question:
            try:
                return await _stream_llm(
                    messages,
                    self.valves.litellm_url,
                    self.valves.litellm_model,
                    self.valves.litellm_api_key,
                )
            except Exception as e:
                traceback.print_exc()
                return f"Chat service error: {e}"

        intent = classify_intent(question)

        if intent == INTENT_CHAT:
            try:
                return await _stream_llm(
                    messages,
                    self.valves.litellm_url,
                    self.valves.litellm_model,
                    self.valves.litellm_api_key,
                )
            except Exception as e:
                traceback.print_exc()
                return f"Chat service error: {e}"

        if intent == INTENT_AMBIGUOUS:
            return (
                "That sounds data-related — do you want me to run an analytics "
                "query on the NYC taxi dataset? If so, please describe what you'd "
                "like to know (e.g. 'show monthly revenue trend' or 'top boroughs by trips')."
            )

        # INTENT_ANALYTICS — return the async generator directly so Open WebUI
        # streams chunks live. Buffering with "".join(chunks) defeats the entire
        # streaming pipeline (registry + supervisor + DuckDB + chart + summary
        # easily totals 10–30s end-to-end).
        return _stream_analytics(
            question,
            self.valves.s3_bucket,
            self.valves.aws_region,
            self.valves.litellm_url,
            self.valves.litellm_model,
            self.valves.litellm_api_key,
            self.valves.registry_ttl,
            self.valves.duckdb_timeout,
            self.valves.row_cap,
            __event_emitter__,
        )
