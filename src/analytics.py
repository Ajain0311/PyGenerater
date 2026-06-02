"""Topic scoring, cost tracking, and performance analytics."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from src.utils import get_logger

log = get_logger(__name__)

# Gemini 1.5 Pro pricing (USD per 1M tokens, as of mid-2024)
GEMINI_INPUT_PRICE_PER_M = 3.50
GEMINI_OUTPUT_PRICE_PER_M = 10.50


def score_topic(keyword: str, rank: int, total: int, related_queries: list[str] | None = None) -> float:
    """
    Score a trending topic on 0-100 scale.
    Higher rank (lower number = more trending) → higher score.
    """
    if total == 0:
        return 0.0

    # Position score: rank 1 → 100, rank N → ~0
    position_score = ((total - rank) / total) * 60.0

    # Keyword quality: longer, more specific topics tend to make better content
    words = len(keyword.split())
    length_score = min(words * 5, 20)

    # Related queries bonus
    related_bonus = min(len(related_queries or []) * 2, 20)

    total_score = position_score + length_score + related_bonus
    return round(min(total_score, 100.0), 2)


def rank_topics(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort topics by score descending and attach rank."""
    sorted_topics = sorted(topics, key=lambda t: t.get("score", 0), reverse=True)
    for i, t in enumerate(sorted_topics, 1):
        t["rank"] = i
    return sorted_topics


def calculate_gemini_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a Gemini API call."""
    input_cost = (input_tokens / 1_000_000) * GEMINI_INPUT_PRICE_PER_M
    output_cost = (output_tokens / 1_000_000) * GEMINI_OUTPUT_PRICE_PER_M
    return round(input_cost + output_cost, 6)


def build_cost_report(videos: list[Any]) -> dict[str, Any]:
    """Aggregate cost data across all videos."""
    total_input = sum(getattr(v, "gemini_input_tokens", 0) or 0 for v in videos)
    total_output = sum(getattr(v, "gemini_output_tokens", 0) or 0 for v in videos)
    total_cost = sum(getattr(v, "estimated_cost_usd", 0) or 0 for v in videos)
    return {
        "total_videos": len(videos),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": round(total_cost, 4),
        "avg_cost_per_video": round(total_cost / max(len(videos), 1), 4),
    }


def keyword_analytics(topics: list[Any]) -> dict[str, Any]:
    """Extract keyword length, category distribution, etc."""
    categories: dict[str, int] = {}
    word_counts: list[int] = []

    for t in topics:
        cat = getattr(t, "category", None) or "unknown"
        categories[cat] = categories.get(cat, 0) + 1
        word_counts.append(len(getattr(t, "keyword", "").split()))

    avg_words = sum(word_counts) / max(len(word_counts), 1)
    return {
        "total_topics": len(topics),
        "category_distribution": categories,
        "avg_keyword_words": round(avg_words, 2),
    }
