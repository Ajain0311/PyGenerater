"""Fetch trending topics from Google Trends and score them."""

from __future__ import annotations

import time
from typing import Any

# Patch urllib3 2.x incompatibility with pytrends (method_whitelist removed in urllib3 2.0)
try:
    import urllib3.util.retry as _r
    if not getattr(_r.Retry, "_patched_for_pytrends", False):
        _orig = _r.Retry.__init__
        def _patched(self, *a, **kw):
            kw.pop("method_whitelist", None)
            _orig(self, *a, **kw)
        _r.Retry.__init__ = _patched
        _r.Retry._patched_for_pytrends = True
except Exception:
    pass

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
            try:
                # pytrends uses method_whitelist which was renamed in urllib3 2.x
                self._pt = TrendReq(hl="en-US", tz=330, timeout=(10, 25), retries=2, backoff_factor=0.5)
            except TypeError:
                self._pt = TrendReq(hl="en-US", tz=330, timeout=(10, 25))
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

    # pytrends trending_searches() uses full country names, not ISO codes
    _GEO_TO_COUNTRY = {
        "IN": "india", "US": "united_states", "GB": "united_kingdom",
        "AU": "australia", "CA": "canada", "SG": "singapore",
        "DE": "germany", "FR": "france", "JP": "japan", "BR": "brazil",
    }

    def _fetch_daily(self, geo: str) -> list[dict[str, Any]]:
        """Fallback 1: daily trending searches via pytrends."""
        pt = self._client()
        country = self._GEO_TO_COUNTRY.get(geo.upper(), geo.lower())
        try:
            df = pt.trending_searches(pn=country)
            topics: list[dict[str, Any]] = []
            seen: set[str] = set()
            for val in df[0].tolist():
                keyword = str(val).strip()
                if not keyword or keyword in seen:
                    continue
                seen.add(keyword)
                topics.append({"keyword": keyword, "category": "trending",
                               "geo": geo, "related_queries": [], "source": "daily"})
            if topics:
                log.info("Fetched %d daily topics", len(topics))
                return topics[:config.TRENDS_COUNT]
        except Exception as e:
            log.warning("pytrends daily fetch failed: %s — trying RSS", e)

        # Fallback 2: Google Trends RSS (official public feed)
        rss_topics = self._fetch_rss(geo)
        if rss_topics:
            return rss_topics

        # Fallback 3: Curated India seed topics (always available)
        log.warning("All trend sources failed. Using curated seed topics.")
        return self._seed_topics(geo)

    def _fetch_rss(self, geo: str) -> list[dict[str, Any]]:
        """Google Trends daily RSS — public endpoint, no auth required."""
        import xml.etree.ElementTree as ET
        import requests as req
        try:
            url = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
            resp = req.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            topics: list[dict[str, Any]] = []
            for item in root.findall(".//item"):
                title_el = item.find("title")
                if title_el is None or not title_el.text:
                    continue
                topics.append({"keyword": title_el.text.strip(), "category": "trending",
                               "geo": geo, "related_queries": [], "source": "rss"})
            log.info("Fetched %d topics from Trends RSS", len(topics))
            return topics[:config.TRENDS_COUNT]
        except Exception as e:
            log.warning("Trends RSS failed: %s", e)
            return []

    @staticmethod
    def _seed_topics(geo: str) -> list[dict[str, Any]]:
        """Curated evergreen curiosity topics as final fallback.

        These are deliberately mystery/facts-genre topics (not news headlines)
        because the content engine writes hook–reveal–loop scripts; generic
        news keywords like "Bollywood New Movie Release" produce weak scripts."""
        seeds = [
            # ── Animals & nature ────────────────────────────────────────────
            ("Why Tigers Never Attack From The Front", "science"),
            ("The Bird That Sleeps While Flying", "science"),
            ("Octopus Three Hearts Mystery", "science"),
            ("Why Cows Face North While Eating", "science"),
            ("The Immortal Jellyfish Secret", "science"),
            ("King Cobra Afraid Of This Animal", "science"),
            ("Elephants Can Hear Through Their Feet", "science"),
            ("The Fish That Walks On Land In India", "science"),
            ("Why Dogs Tilt Their Head When You Speak", "science"),
            ("Crows Remember Human Faces For Years", "science"),
            ("The Snake That Flies In Indian Forests", "science"),
            ("Why Ants Never Sleep", "science"),
            # ── India pride & history ───────────────────────────────────────
            ("Secrets Hidden Inside The Taj Mahal", "history"),
            ("The Indian Temple Built From A Single Rock", "history"),
            ("Why Indian Railways Never Paints Some Bridges", "history"),
            ("The Village In India Where Doors Have No Locks", "history"),
            ("India's Floating Lake Mystery", "history"),
            ("The 1000 Year Old Indian Temple That Hangs In Air", "history"),
            ("Why The Qutub Minar Iron Pillar Never Rusts", "history"),
            ("The Indian King Who Defeated Alexander's Fear", "history"),
            ("Secret Tunnels Under Delhi Nobody Talks About", "history"),
            ("The Indian Lake That Turns Pink", "science"),
            ("Why Kumbh Mela Is Visible From Space", "history"),
            ("The Indian Village Where Everyone Speaks Sanskrit", "history"),
            # ── Space & science ─────────────────────────────────────────────
            ("What ISRO Found On The Moon's South Pole", "science"),
            ("The Sound Black Holes Actually Make", "science"),
            ("Why Astronauts Grow Taller In Space", "science"),
            ("The Planet Where It Rains Diamonds", "science"),
            ("What Happens If You Fall Into Jupiter", "science"),
            ("The Star That Should Not Exist", "science"),
            ("Why The Ocean Is Deeper Than Everest Is Tall", "science"),
            ("NASA Recorded This Sound From The Sun", "science"),
            # ── Human body & psychology ─────────────────────────────────────
            ("Your Brain Deletes Memories While You Sleep", "health"),
            ("Why Your Stomach Has A Second Brain", "health"),
            ("The Real Reason You Forget Why You Entered A Room", "health"),
            ("Humans Glow In The Dark But Can't See It", "science"),
            ("Why Time Feels Faster As You Get Older", "health"),
            ("Your Body Replaces Itself Every 7 Years", "health"),
            ("Why Goosebumps Exist", "health"),
            # ── Technology & AI ─────────────────────────────────────────────
            ("AI Predicted This About India By 2030", "technology"),
            ("Why Your Phone Battery Dies Faster Every Year", "technology"),
            ("The Indian Engineer Behind The Pentium Chip", "technology"),
            ("What Happens To Deleted Photos Really", "technology"),
            ("The Computer Older Than Your Grandfather Still Running", "technology"),
            ("Why Aeroplane Windows Are Always Round", "technology"),
            ("The Indian App That Beat WhatsApp In One Country", "technology"),
            # ── Food & daily life ───────────────────────────────────────────
            ("Why Indian Train Chai Tastes Different", "food"),
            ("The Real Reason Maggi Takes Exactly 2 Minutes", "food"),
            ("Why Banana Is A Berry But Strawberry Is Not", "food"),
            ("The Spice Worth More Than Gold In India", "food"),
            ("Why Hotel Food Tastes Better At Night", "food"),
            ("The Indian Sweet That Is 500 Years Old", "food"),
            # ── Money & world ───────────────────────────────────────────────
            ("Why Indian Coins Have Different Shapes", "finance"),
            ("The Country Where India Built A Whole City", "business"),
            ("Why 1 Rupee Note Is Signed Differently", "finance"),
            ("The Indian Family Richer Than Some Countries", "business"),
            ("Why Petrol Prices Change At 6 AM In India", "finance"),
            ("The Most Expensive House In The World Is In Mumbai", "business"),
            # ── Geography & mystery ─────────────────────────────────────────
            ("The Indian Border Visible From Space At Night", "geography"),
            ("Why No Bridge Exists Over The Amazon River", "geography"),
            ("The Indian Village On The Border Of Two Countries", "geography"),
            ("The Place In India Where Gravity Fails", "mystery"),
            ("The Indian Lake With A Floating Island Post Office", "geography"),
            ("Why Magnetic Hill In Ladakh Pulls Cars Uphill", "mystery"),
        ]
        return [{"keyword": k, "category": c, "geo": geo,
                 "related_queries": [], "source": "seed"} for k, c in seeds]

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
