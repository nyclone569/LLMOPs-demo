import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import (
    classify_intent,
    INTENT_ANALYTICS,
    INTENT_AMBIGUOUS,
    INTENT_CHAT,
    _normalize_duckdb_sql,
    _select_presentation_mode,
)
from filter_analytics import build_html_artifact, chart_spec_to_vegalite


def test_routes_analytics_on_domain_plus_analytics_signal():
    assert classify_intent("show monthly revenue trend for taxi trips") == INTENT_ANALYTICS


def test_routes_analytics_on_borough_and_total():
    assert classify_intent("what is the total fare by borough") == INTENT_ANALYTICS


def test_routes_analytics_on_top_zones():
    assert classify_intent("top pickup zones by revenue") == INTENT_ANALYTICS


def test_routes_analytics_on_explicit_underscored_table_name():
    assert classify_intent("Show me table kpi_zone_net_flow") == INTENT_ANALYTICS


def test_routes_ambiguous_on_domain_only():
    assert classify_intent("what about taxi") == INTENT_AMBIGUOUS


def test_routes_ambiguous_on_borough_only():
    assert classify_intent("tell me about manhattan") == INTENT_AMBIGUOUS


def test_routes_chat_on_no_domain_signal():
    assert classify_intent("how do I write a Python function") == INTENT_CHAT


def test_routes_chat_on_generic_greeting():
    assert classify_intent("hello, how are you?") == INTENT_CHAT


def test_false_positive_total_without_domain():
    assert classify_intent("what is the total cost of this project") == INTENT_CHAT


def test_false_positive_compare_without_domain():
    assert classify_intent("compare these two code snippets") == INTENT_CHAT


def test_routes_analytics_on_peak_hours():
    assert classify_intent("show peak hour trips daily") == INTENT_ANALYTICS


def test_routes_analytics_case_insensitive():
    assert classify_intent("SHOW MONTHLY REVENUE FOR TAXI") == INTENT_ANALYTICS


def test_no_false_positive_farewell():
    assert classify_intent("show farewell message to the team") == INTENT_CHAT


def test_no_false_positive_per_in_performance():
    assert classify_intent("check system performance metrics") == INTENT_CHAT


def test_select_presentation_mode_table_prompt():
    rows = [{"month": "Jan", "revenue": 1000}]
    assert _select_presentation_mode("show monthly revenue as a table", rows) == "table"


def test_select_presentation_mode_chart_prompt():
    rows = [{"month": "Jan", "revenue": 1000}]
    assert _select_presentation_mode("plot monthly revenue as a chart", rows) == "chart"


def test_select_presentation_mode_both_prompt():
    rows = [{"borough": "Manhattan", "revenue": 1000}]
    assert (
        _select_presentation_mode("show revenue by borough with chart and table", rows)
        == "both"
    )


def test_select_presentation_mode_empty_rows_is_text():
    assert _select_presentation_mode("show monthly revenue as a table", []) == "text"


def test_select_presentation_mode_no_display_preference_is_auto():
    rows = [{"month": "Jan", "revenue": 1000}]
    assert _select_presentation_mode("show monthly revenue trend", rows) == "auto"


def test_normalize_duckdb_sql_rewrites_mysql_current_date_subtract_interval():
    sql = (
        "SELECT pickup_date, revenue FROM kpi_daily_overview "
        "WHERE pickup_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)"
    )

    normalized = _normalize_duckdb_sql(sql)

    assert "DATE_SUB" not in normalized
    assert "CURRENT_DATE()" not in normalized
    assert "pickup_date >= CURRENT_DATE - INTERVAL 7 DAY" in normalized


def test_html_artifact_built_from_bar_chart_spec():
    chart_spec = {"type": "bar", "x": "month", "y": "revenue", "series": []}
    rows = [{"month": "Jan", "revenue": 1000}]
    html = build_html_artifact(chart_spec, rows)
    assert "<!DOCTYPE html>" in html
    assert "vegaEmbed" in html
    assert '"mark": "bar"' in html


def test_html_artifact_built_from_line_chart_spec():
    chart_spec = {"type": "line", "x": "date", "y": "trip_count", "series": []}
    rows = [{"date": "2023-01", "trip_count": 500}]
    html = build_html_artifact(chart_spec, rows)
    assert '"mark": "line"' in html


def test_html_artifact_pie_renders_as_horizontal_bar():
    chart_spec = {"type": "pie", "x": "borough", "y": "fare", "series": []}
    rows = [{"borough": "Manhattan", "fare": 5000}]
    html = build_html_artifact(chart_spec, rows)
    assert '"mark": "bar"' in html


def test_chart_type_table_builds_table_artifact():
    chart_spec = {"type": "table", "x": "month", "y": "revenue", "series": []}
    rows = [{"month": "Jan", "revenue": 1000}]
    result = build_html_artifact(chart_spec, rows)

    assert result is not None
    assert "<!DOCTYPE html>" in result
    assert "Query result table" in result
    assert "data-analytics-table" in result
    assert "Download" not in result


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


def test_chart_spec_to_vegalite_bar():
    spec = chart_spec_to_vegalite({"type": "bar", "x": "month", "y": "revenue"}, [{"month": "Jan", "revenue": 100}])
    assert spec["mark"] == "bar"
    assert spec["encoding"]["x"]["field"] == "month"
    assert spec["encoding"]["y"]["field"] == "revenue"
    assert spec["encoding"]["x"]["type"] == "ordinal"
    assert spec["encoding"]["y"]["type"] == "quantitative"


def test_chart_spec_to_vegalite_line_uses_temporal():
    spec = chart_spec_to_vegalite({"type": "line", "x": "date", "y": "trip_count"}, [{"date": "2023-01", "trip_count": 500}])
    assert spec["mark"] == "line"
    assert spec["encoding"]["x"]["type"] == "temporal"
    assert spec["encoding"]["y"]["type"] == "quantitative"
