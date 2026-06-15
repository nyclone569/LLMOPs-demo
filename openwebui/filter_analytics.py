"""
title: NYC Taxi Analytics Filter
author: llmops
version: 1.0.0
license: MIT
requirements: duckdb==1.2.2
"""

from __future__ import annotations

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
