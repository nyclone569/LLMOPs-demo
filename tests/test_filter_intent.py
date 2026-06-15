import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import classify_intent, INTENT_ANALYTICS, INTENT_AMBIGUOUS, INTENT_CHAT
from filter_analytics import build_html_artifact, chart_spec_to_vegalite


def test_routes_analytics_on_domain_plus_analytics_signal():
    assert classify_intent("show monthly revenue trend for taxi trips") == INTENT_ANALYTICS


def test_routes_analytics_on_borough_and_total():
    assert classify_intent("what is the total fare by borough") == INTENT_ANALYTICS


def test_routes_analytics_on_top_zones():
    assert classify_intent("top pickup zones by revenue") == INTENT_ANALYTICS


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


def test_no_html_when_chart_type_is_table():
    chart_spec = {"type": "table", "x": "month", "y": "revenue", "series": []}
    rows = [{"month": "Jan", "revenue": 1000}]
    result = build_html_artifact(chart_spec, rows)
    assert result is None


def test_chart_spec_to_vegalite_bar():
    spec = chart_spec_to_vegalite({"type": "bar", "x": "month", "y": "revenue"}, [{"month": "Jan", "revenue": 100}])
    assert spec["mark"] == "bar"
    assert spec["encoding"]["x"]["field"] == "month"
    assert spec["encoding"]["y"]["field"] == "revenue"
    assert spec["encoding"]["x"]["type"] == "ordinal"
    assert spec["encoding"]["y"]["type"] == "quantitative"
