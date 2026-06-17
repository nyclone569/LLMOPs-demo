# Open WebUI Query Result Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a view-only interactive query result table artifact to the existing Open WebUI analytics pipe.

**Architecture:** Keep the existing analytics path in `openwebui/filter_analytics.py`: intent router, supervisor, query agent, DuckDB execution, and summary streaming remain in place. Add a pipe-local presentation layer that chooses chart/table/both/text, builds a self-contained HTML table artifact with vanilla HTML/CSS/JS, streams the summary first, then emits chart/table artifacts through the existing Open WebUI `embeds` event path.

**Tech Stack:** Python 3.12, pytest, Starlette `StreamingResponse`, Open WebUI Pipe events, vanilla HTML/CSS/JavaScript inside self-contained HTML artifacts.

---

## File Structure

- Modify `openwebui/filter_analytics.py`
  - Add `import html as html_lib`.
  - Add presentation constants and `_select_presentation_mode(question, rows)`.
  - Add `_infer_table_columns(rows)` and `build_table_artifact(rows, metadata)`.
  - Update `build_html_artifact(chart_spec, rows)` so `chart_spec.type == "table"` delegates to `build_table_artifact`.
  - Update `_stream_analytics` to prepare chart/table artifacts, stream summary, then emit artifacts.

- Modify `tests/test_filter_intent.py`
  - Add unit tests for `_select_presentation_mode`.
  - Replace the current `chart_spec.type == "table"` expectation from "no HTML" to "table HTML artifact".
  - Add table artifact content and escaping tests.

- Modify `tests/test_filter_pipeline.py`
  - Add stream tests for table-only and both chart/table artifact emission.
  - Add a stream-order test proving summary content is yielded before artifact emission.

No new services, manifests, frontend packages, or docs are required for implementation.

---

### Task 1: Add Presentation Mode Selection

**Files:**
- Modify: `tests/test_filter_intent.py`
- Modify: `openwebui/filter_analytics.py`

- [ ] **Step 1: Add failing tests for presentation mode**

Add this import to `tests/test_filter_intent.py`:

```python
from filter_analytics import _select_presentation_mode
```

Add these tests near the intent-routing tests:

```python
def test_select_presentation_mode_table_prompt():
    rows = [{"month": "Jan", "revenue": 1000}]
    assert _select_presentation_mode("show monthly revenue as a table", rows) == "table"


def test_select_presentation_mode_chart_prompt():
    rows = [{"month": "Jan", "revenue": 1000}]
    assert _select_presentation_mode("plot monthly revenue as a chart", rows) == "chart"


def test_select_presentation_mode_both_prompt():
    rows = [{"borough": "Manhattan", "revenue": 1000}]
    assert _select_presentation_mode("show revenue by borough with chart and table", rows) == "both"


def test_select_presentation_mode_empty_rows_is_text():
    assert _select_presentation_mode("show monthly revenue as a table", []) == "text"


def test_select_presentation_mode_no_display_preference_is_auto():
    rows = [{"month": "Jan", "revenue": 1000}]
    assert _select_presentation_mode("show monthly revenue trend", rows) == "auto"
```

- [ ] **Step 2: Run presentation mode tests and verify failure**

Run:

```bash
pytest tests/test_filter_intent.py::test_select_presentation_mode_table_prompt \
  tests/test_filter_intent.py::test_select_presentation_mode_chart_prompt \
  tests/test_filter_intent.py::test_select_presentation_mode_both_prompt \
  tests/test_filter_intent.py::test_select_presentation_mode_empty_rows_is_text \
  tests/test_filter_intent.py::test_select_presentation_mode_no_display_preference_is_auto -v
```

Expected: FAIL with an import error like `cannot import name '_select_presentation_mode'`.

- [ ] **Step 3: Implement presentation mode helper**

In `openwebui/filter_analytics.py`, add these constants after `ANALYTICS_WORDS`:

```python
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
```

Add this helper after `classify_intent`:

```python
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
```

- [ ] **Step 4: Run presentation mode tests and verify pass**

Run:

```bash
pytest tests/test_filter_intent.py::test_select_presentation_mode_table_prompt \
  tests/test_filter_intent.py::test_select_presentation_mode_chart_prompt \
  tests/test_filter_intent.py::test_select_presentation_mode_both_prompt \
  tests/test_filter_intent.py::test_select_presentation_mode_empty_rows_is_text \
  tests/test_filter_intent.py::test_select_presentation_mode_no_display_preference_is_auto -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit presentation mode helper**

Run:

```bash
git add openwebui/filter_analytics.py tests/test_filter_intent.py
git commit -m "feat: select analytics presentation mode"
```

Expected: commit includes only the presentation-mode tests and helper.

---

### Task 2: Add Self-Contained Table Artifact Builder

**Files:**
- Modify: `tests/test_filter_intent.py`
- Modify: `openwebui/filter_analytics.py`

- [ ] **Step 1: Add failing tests for table artifact HTML**

Update the existing `test_no_html_when_chart_type_is_table` in `tests/test_filter_intent.py` to:

```python
def test_chart_type_table_builds_table_artifact():
    chart_spec = {"type": "table", "x": "month", "y": "revenue", "series": []}
    rows = [{"month": "Jan", "revenue": 1000}]
    result = build_html_artifact(chart_spec, rows)

    assert result is not None
    assert "<!DOCTYPE html>" in result
    assert "Query result table" in result
    assert "data-analytics-table" in result
    assert "Download" not in result
```

Add these tests below it:

```python
def test_table_artifact_escapes_values_and_embeds_rows_json():
    from filter_analytics import build_table_artifact

    rows = [{"zone": "<script>alert(1)</script>", "revenue": 12.5}]
    html = build_table_artifact(rows, {"row_cap": 200, "capped": False})

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert '<script type="application/json" id="table-data">' in html
    assert '"revenue": 12.5' in html


def test_table_artifact_has_search_sort_pagination_and_no_export():
    from filter_analytics import build_table_artifact

    rows = [{"month": "Jan", "revenue": 1000}]
    html = build_table_artifact(rows, {"row_cap": 200, "capped": True})

    assert 'id="global-search"' in html
    assert 'id="page-size"' in html
    assert 'id="prev-page"' in html
    assert 'id="next-page"' in html
    assert "sortState" in html
    assert "Showing first 200 rows" in html
    assert "CSV" not in html
    assert "download" not in html.lower()
```

- [ ] **Step 2: Run table artifact tests and verify failure**

Run:

```bash
pytest tests/test_filter_intent.py::test_chart_type_table_builds_table_artifact \
  tests/test_filter_intent.py::test_table_artifact_escapes_values_and_embeds_rows_json \
  tests/test_filter_intent.py::test_table_artifact_has_search_sort_pagination_and_no_export -v
```

Expected: FAIL because `build_table_artifact` does not exist and `build_html_artifact` returns `None` for table type.

- [ ] **Step 3: Add HTML escaping import**

In `openwebui/filter_analytics.py`, add this import with the other imports:

```python
import html as html_lib
```

- [ ] **Step 4: Implement table columns and artifact builder**

In `openwebui/filter_analytics.py`, add these helpers above `build_html_artifact`:

```python
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
        <tbody id="table-body"></tbody>
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
```

- [ ] **Step 5: Route table chart type to table artifact**

Change the beginning of `build_html_artifact` in `openwebui/filter_analytics.py` from:

```python
    if chart_spec.get("type") == "table":
        return None
```

to:

```python
    if chart_spec.get("type") == "table":
        return build_table_artifact(rows, {"row_cap": ROW_CAP, "capped": False})
```

- [ ] **Step 6: Run table artifact tests and verify pass**

Run:

```bash
pytest tests/test_filter_intent.py::test_chart_type_table_builds_table_artifact \
  tests/test_filter_intent.py::test_table_artifact_escapes_values_and_embeds_rows_json \
  tests/test_filter_intent.py::test_table_artifact_has_search_sort_pagination_and_no_export -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit table artifact builder**

Run:

```bash
git add openwebui/filter_analytics.py tests/test_filter_intent.py
git commit -m "feat: build analytics table artifact"
```

Expected: commit includes table artifact implementation and tests only.

---

### Task 3: Update Analytics Streaming To Emit Summary Before Artifacts

**Files:**
- Modify: `tests/test_filter_pipeline.py`
- Modify: `openwebui/filter_analytics.py`

- [ ] **Step 1: Add failing table-only stream test**

Add this test below `test_stream_analytics_yields_reasoning_trace_and_summary` in `tests/test_filter_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_stream_analytics_table_prompt_emits_table_artifact_after_summary():
    rows = [{"pickup_month": 1, "total_revenue": 10.0}]
    registry = {"kpi_monthly_summary": {"tier": "kpi", "columns": [{"name": "pickup_month", "type": "int32"}, {"name": "total_revenue", "type": "double"}], "example_questions": [], "description": "Monthly summary"}}

    emitted = []
    summary_complete = False

    async def mock_emitter(event):
        emitted.append({"event": event, "summary_complete": summary_complete})

    async def fake_summary(*args, **kwargs):
        nonlocal summary_complete
        yield "Revenue summary."
        summary_complete = True

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "Monthly revenue question"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT pickup_month, total_revenue FROM kpi_monthly_summary", "rows": rows, "capped": False}), \
         patch("filter_analytics._run_chart_spec") as mock_chart_spec, \
         patch("filter_analytics.build_table_artifact", return_value="<html>table</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_summary()):

        chunks = []
        async for chunk in _stream_analytics("show monthly revenue as a table", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    assert "Revenue summary." in "".join(chunks)
    mock_chart_spec.assert_not_called()
    embed_events = [entry for entry in emitted if entry["event"]["type"] == "embeds"]
    assert len(embed_events) == 1
    assert embed_events[0]["summary_complete"] is True
    assert embed_events[0]["event"]["data"]["embeds"] == ["<html>table</html>"]
```

- [ ] **Step 2: Add failing both-mode stream test**

Add this test below the table-only test:

```python
@pytest.mark.asyncio
async def test_stream_analytics_both_prompt_emits_chart_and_table_artifacts():
    rows = [{"borough": "Manhattan", "total_revenue": 10.0}]
    registry = {"kpi_borough_comparison": {"tier": "kpi", "columns": [{"name": "borough", "type": "varchar"}, {"name": "total_revenue", "type": "double"}], "example_questions": [], "description": "Borough comparison"}}

    emitted = []

    async def mock_emitter(event):
        emitted.append(event)

    async def fake_summary(*args, **kwargs):
        yield "Revenue summary."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_borough_comparison", "confidence": "high", "reasoning": "Borough revenue question"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT borough, total_revenue FROM kpi_borough_comparison", "rows": rows, "capped": True}), \
         patch("filter_analytics._run_chart_spec", return_value={"type": "bar", "x": "borough", "y": "total_revenue"}), \
         patch("filter_analytics.build_html_artifact", return_value="<html>chart</html>"), \
         patch("filter_analytics.build_table_artifact", return_value="<html>table</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_summary()):

        chunks = []
        async for chunk in _stream_analytics("show revenue by borough with chart and table", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    assert "Revenue summary." in "".join(chunks)
    embed_events = [event for event in emitted if event["type"] == "embeds"]
    assert len(embed_events) == 1
    assert embed_events[0]["data"]["embeds"] == ["<html>chart</html>", "<html>table</html>"]
```

- [ ] **Step 3: Add failing auto table fallback test**

Add this test below the both-mode test:

```python
@pytest.mark.asyncio
async def test_stream_analytics_auto_table_chart_spec_emits_table_artifact():
    rows = [{"month": "Jan", "total_revenue": 10.0}]
    registry = {"kpi_monthly_summary": {"tier": "kpi", "columns": [{"name": "month", "type": "varchar"}, {"name": "total_revenue", "type": "double"}], "example_questions": [], "description": "Monthly summary"}}

    emitted = []

    async def mock_emitter(event):
        emitted.append(event)

    async def fake_summary(*args, **kwargs):
        yield "Revenue summary."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "Monthly revenue question"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT month, total_revenue FROM kpi_monthly_summary", "rows": rows, "capped": False}), \
         patch("filter_analytics._run_chart_spec", return_value={"type": "table", "x": "month", "y": "total_revenue"}), \
         patch("filter_analytics.build_table_artifact", return_value="<html>table</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_summary()):

        chunks = []
        async for chunk in _stream_analytics("show monthly revenue", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    assert "Revenue summary." in "".join(chunks)
    embed_events = [event for event in emitted if event["type"] == "embeds"]
    assert len(embed_events) == 1
    assert embed_events[0]["data"]["embeds"] == ["<html>table</html>"]
```

- [ ] **Step 4: Run new stream tests and verify failure**

Run:

```bash
pytest tests/test_filter_pipeline.py::test_stream_analytics_table_prompt_emits_table_artifact_after_summary \
  tests/test_filter_pipeline.py::test_stream_analytics_both_prompt_emits_chart_and_table_artifacts \
  tests/test_filter_pipeline.py::test_stream_analytics_auto_table_chart_spec_emits_table_artifact -v
```

Expected: FAIL because `_stream_analytics` still emits chart artifacts before summary and has no presentation branch.

- [ ] **Step 5: Replace chart-only branch with presentation artifact preparation**

In `openwebui/filter_analytics.py`, replace this block inside `_stream_analytics`:

```python
    if emitter:
        await emitter({"type": "status", "data": {"description": f"Queried {len(rows)} rows — preparing chart...", "done": False}})
    try:
        chart_spec = _run_chart_spec(question, rows, litellm_url, litellm_model, api_key)
        if chart_spec:
            html = build_html_artifact(chart_spec, rows)
            if html and emitter:
                await emitter({"type": "embeds", "data": {"embeds": [html]}})
    except Exception:
        pass

    if emitter:
        await emitter({"type": "status", "data": {"description": "Writing summary...", "done": False}})
    yield "---\n\n"
```

with this block:

```python
    artifacts: list[str] = []
    artifact_note = ""
    mode = _select_presentation_mode(question, rows)

    if emitter:
        await emitter({"type": "status", "data": {"description": f"Queried {len(rows)} rows — preparing response...", "done": False}})

    try:
        chart_spec = None
        if mode in {"chart", "both", "auto"}:
            chart_spec = _run_chart_spec(question, rows, litellm_url, litellm_model, api_key)

        if mode == "auto" and chart_spec and chart_spec.get("type") == "table":
            mode = "table"
        elif mode == "auto" and chart_spec:
            mode = "chart"
        elif mode == "auto":
            mode = "table"

        if mode in {"chart", "both"} and chart_spec:
            chart_html = build_html_artifact(chart_spec, rows)
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

    if emitter:
        await emitter({"type": "status", "data": {"description": "Writing summary...", "done": False}})
    yield "---\n\n"
```

Then, after the summary streaming try/except block and before `yield "\n"`, add:

```python
    if artifact_note:
        yield artifact_note

    if artifacts and emitter:
        await emitter({"type": "embeds", "data": {"embeds": artifacts}})
```

- [ ] **Step 6: Run new stream tests and verify pass**

Run:

```bash
pytest tests/test_filter_pipeline.py::test_stream_analytics_table_prompt_emits_table_artifact_after_summary \
  tests/test_filter_pipeline.py::test_stream_analytics_both_prompt_emits_chart_and_table_artifacts \
  tests/test_filter_pipeline.py::test_stream_analytics_auto_table_chart_spec_emits_table_artifact -v
```

Expected: 3 passed.

- [ ] **Step 7: Run existing stream analytics tests**

Run:

```bash
pytest tests/test_filter_pipeline.py::test_stream_analytics_yields_reasoning_trace_and_summary \
  tests/test_filter_pipeline.py::test_stream_analytics_yields_clarification_on_low_confidence \
  tests/test_filter_pipeline.py::test_stream_analytics_yields_error_on_query_failure \
  tests/test_filter_pipeline.py::test_full_pipe_integration_streams_trace_and_summary -v
```

Expected: 4 passed.

- [ ] **Step 8: Commit streaming presentation branch**

Run:

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "feat: emit analytics table artifacts"
```

Expected: commit includes `_stream_analytics` orchestration changes and stream tests only.

---

### Task 4: Run Full Verification And Smoke Checklist

**Files:**
- Verify: `openwebui/filter_analytics.py`
- Verify: `tests/test_filter_intent.py`
- Verify: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Run focused test files**

Run:

```bash
pytest tests/test_filter_intent.py tests/test_filter_pipeline.py -v
```

Expected: all tests in both files pass.

- [ ] **Step 2: Run full test suite**

Run:

```bash
pytest -v
```

Expected: all tests pass.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected:

- Modified files are limited to `openwebui/filter_analytics.py`, `tests/test_filter_intent.py`, and `tests/test_filter_pipeline.py`.
- Existing unrelated dirty files from before this work remain unrelated and are not staged.

- [ ] **Step 4: Manually review artifact constraints**

Run:

```bash
grep -nE "DataTables|Tabulator|Grid|jquery|cdn|download|CSV|copy-all" openwebui/filter_analytics.py
```

Expected:

- No matches for external table libraries or CDN.
- No download/export controls in the table artifact.
- A match for `download` is acceptable only if it appears in a test assertion that verifies the word is absent from generated HTML.

- [ ] **Step 5: Confirm there are no extra verification changes**

Run:

```bash
git diff --exit-code -- openwebui/filter_analytics.py tests/test_filter_intent.py tests/test_filter_pipeline.py
```

Expected: exit 0. If this command exits nonzero, inspect the diff and either commit the already-tested implementation changes with the Task 3 commit command or revert accidental edits before continuing.

- [ ] **Step 6: Manual WebUI smoke test**

In Open WebUI, select the analytics pipe model and run:

```text
show monthly revenue trend
```

Expected:

- Summary streams first.
- One chart artifact appears below the summary.

Run:

```text
show monthly revenue trend as a table
```

Expected:

- Summary streams first.
- One table artifact appears below the summary.
- Table search, sort, page size, previous, and next controls work over displayed rows.
- No CSV/download control is visible.

Run:

```text
show revenue by borough with chart and table
```

Expected:

- Summary streams first.
- Chart and table artifacts appear below the summary.
- Table controls work over displayed rows.

---

## Self-Review Checklist

- Spec coverage:
  - HTML artifact table: Task 2.
  - Summary first: Task 3.
  - Prompt-driven chart/table/both/text: Task 1 and Task 3.
  - View-only, no CSV/download: Task 2 and Task 4.
  - 200-row cap: existing `ROW_CAP`, verified in Task 2 metadata and Task 4.
  - Existing analytics path unchanged: Task 3 limits changes to presentation branch.
  - Tests and smoke flow: Task 4.

- Placeholder scan:
  - No banned marker steps.
  - Each implementation step includes concrete code or exact commands.

- Type consistency:
  - `_select_presentation_mode(question, rows)` returns `chart`, `table`, `both`, `text`, or `auto`.
  - `build_table_artifact(rows, metadata)` returns an HTML string.
  - `_stream_analytics` uses existing `row_cap` and `capped` values when building table metadata.
