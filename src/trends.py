"""Fetch trending topics from Google Trends and score them."""

from __future__ import annotations

import time
from typing import Any

from pytrends.request import TrendReq

from src.analytics import score_topic, rank_topics
from src.config import config
from src.utils import api_retry, get_logger

log = get_logger(__name__)


class TrendsFetcher:
    def __init__(self):
        self._pt: TrendReq | None = None

    def _client(self) -> TrendReq:
        if self._pt is None:
            self._pt = TrendReq(hl="en-US", tz=330, timeout=(10, 25), retries=2, backoff_factor=0.5)
        return self._pt

    @api_retry(max_attempts=3, wait_min=5, wait_max=60, exceptions=(Exception,))
    def fetch_realtime(self, geo: str | None = None) -> list[dict[str, Any]]:
        """Fetch real-time trending searches for the given geo."""
        geo = geo or config.TRENDS_GEO
        log.info("Fetching real-time trends for geo=%s", geo)
        pt = self._client()

        try:
            df = pt.realtime_trending_searches(pn=geo)
            topics: list[dict[str, Any]] = []
            seen: set[str] = set()

            for idx, row in df.iterrows():
                title = str(row.get("title", "")).strip()
                if not title or title in seen:
                    continue
                seen.add(title)

                entity_names = row.get("entityNames", [])
                related = entity_names if isinstance(entity_names, list) else []

                topics.append(
                    {
                        "keyword": title,
                        "category": str(row.get("formattedTrafficSources", "Trending")),
                        "geo": geo,
                        "related_queries": related,
                        "source": "realtime",
                    }
                )

            log.info("Fetched %d real-time topics", len(topics))
            return topics[:config.TRENDS_COUNT]

        except Exception as e:
            log.warning("Real-time trends failed (%s), falling back to daily trends", e)
            return self._fetch_daily(geo)

    def _fetch_daily(self, geo: str) -> list[dict[str, Any]]:
        """Fallback: daily trending searches."""
        pt = self._client()
        try:
            df = pt.trending_searches(pn=geo.lower())
            topics: list[dict[str, Any]] = []
            seen: set[str] = set()

            for val in df[0].tolist():
                keyword = str(val).strip()
                if not keyword or keyword in seen:
                    continue
                seen.add(keyword)
                topics.append(
                    {
                        "keyword": keyword,
                        "category": "trending",
                        "geo": geo,
                        "related_queries": [],
                        "source": "daily",
                    }
                )

            log.info("Fetched %d daily topics", len(topics))
            return topics[:config.TRENDS_COUNT]
        except Exception as e:
            log.error("Daily trends also failed: %s", e)
            return []

    def fetch_related_queries(self, keyword: str) -> dict[str, Any]:
        """Get related queries and search volume for a keyword."""
        try:
            pt = self._client()
            pt.build_payload([keyword], timeframe="now 1-d", geo=config.TRENDS_GEO)
            related = pt.related_queries()
            result = related.get(keyword, {})
            top_df = result.get("top")
            rising_df = result.get("rising")

            top = top_df.to_dict("records") if top_df is not None and not top_df.empty else []
            rising = rising_df.to_dict("records") if rising_df is not None and not rising_df.empty else []
            return {"top": top, "rising": rising}
        except Exception as e:
            log.debug("Could not fetch related queries for %r: %s", keyword, e)
            return {"top": [], "rising": []}

    def score_and_rank(self, raw_topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add score and rank to topics, return sorted list."""
        total = len(raw_topics)
        for rank, topic in enumerate(raw_topics, 1):
            topic["score"] = score_topic(
                keyword=topic["keyword"],
                rank=rank,
                total=total,
                related_queries=topic.get("related_queries", []),
            )
        return rank_topics(raw_topics)

    def get_scored_topics(
        self,
        exclude_keywords: set[str] | None = None,
        geo: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Full pipeline: fetch → filter duplicates → score → rank.
        Returns list of topic dicts ready for DB insertion.
        """
        raw = self.fetch_realtime(geo)
        if not raw:
            log.warning("No topics fetched.")
            return []

        exclude = exclude_keywords or set()
        filtered = [t for t in raw if t["keyword"] not in exclude]
        log.info("%d topics after duplicate filter (%d excluded)", len(filtered), len(raw) - len(filtered))

        scored = self.score_and_rank(filtered)
        return scored
