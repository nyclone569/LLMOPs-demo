import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import classify_intent, INTENT_ANALYTICS, INTENT_AMBIGUOUS, INTENT_CHAT


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
