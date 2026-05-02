"""News Monitor for Striker Module.

Polls news APIs, computes fear scores using sentiment analysis,
and detects fear/greed spikes for trade signals.
"""

import os
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
from textblob import TextBlob

from ..utils.logging_config import get_logger

logger = get_logger("striker.news")

# Keywords associated with negative market sentiment
NEGATIVE_KEYWORDS = [
    "crash", "plunge", "collapse", "panic", "sell-off", "bearish",
    "recession", "war", "conflict", "sanctions", "tariff", "inflation",
    "hack", "exploit", "fraud", "scam", "ban", "crackdown",
    "Iran", "oil", "Fed", "interest rate",
]

# Keywords associated with positive market sentiment
POSITIVE_KEYWORDS = [
    "surge", "rally", "moon", "bullish", "breakout", "adoption",
    "partnership", "integration", "approval", "launch",
]


class NewsMonitor:
    """Monitors news feeds for sentiment-driven trading signals."""

    def __init__(self, config: Dict = None):
        """Initialize the news monitor.

        Args:
            config: Configuration dict with API keys and settings.
        """
        self.config = config or {}
        self.news_api_key = os.environ.get("NEWSAPI_KEY", "")
        self.keywords = self.config.get("keywords", NEGATIVE_KEYWORDS)
        self.fear_threshold = self.config.get("fear_threshold", 70)
        self.greed_threshold = self.config.get("greed_threshold", 20)
        self.spike_increase = self.config.get("spike_increase", 30)
        self.poll_interval_minutes = self.config.get("poll_interval_minutes", 15)

        # Store fear scores over time for spike detection
        self.fear_history: deque = deque(maxlen=96)  # ~24 hours at 15-min intervals
        self.last_fear_score: Optional[float] = None
        self.latest_headlines: List[Dict] = []

    def fetch_news(
        self,
        query: str = None,
        language: str = "en",
        page_size: int = 50,
    ) -> List[Dict]:
        """Fetch news articles from NewsAPI.

        Args:
            query: Search query (None for general market news).
            language: Language code.
            page_size: Number of articles to fetch.

        Returns:
            List of article dicts.
        """
        if not self.news_api_key:
            logger.warning("NewsAPI key not configured. Using simulated headlines.")
            return self._generate_simulated_headlines()

        try:
            base_url = "https://newsapi.org/v2/everything"
            params = {
                "q": query or "cryptocurrency OR crypto market",
                "language": language,
                "pageSize": page_size,
                "sortBy": "publishedAt",
                "apiKey": self.news_api_key,
            }

            response = httpx.get(base_url, params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json()

            articles = data.get("articles", [])
            logger.info("Fetched %d articles from NewsAPI", len(articles))

            result = []
            for article in articles:
                result.append({
                    "title": article.get("title", ""),
                    "description": article.get("description", ""),
                    "url": article.get("url", ""),
                    "published_at": article.get("publishedAt", ""),
                    "source": article.get("source", {}).get("name", ""),
                })

            return result

        except httpx.HTTPError as e:
            logger.error("Failed to fetch news from NewsAPI: %s", e)
            return self._generate_simulated_headlines()
        except Exception as e:
            logger.error("Unexpected error fetching news: %s", e)
            return self._generate_simulated_headlines()

    def compute_sentiment_score(self, text: str) -> float:
        """Compute sentiment score for a text using TextBlob.

        Args:
            text: Input text to analyze.

        Returns:
            Sentiment polarity score between -1.0 (negative) and 1.0 (positive).
        """
        analysis = TextBlob(text)
        return analysis.sentiment.polarity

    def compute_fear_score(self, articles: List[Dict]) -> float:
        """Compute a fear score (0-100) based on article sentiment.

        Args:
            articles: List of article dicts with title and description.

        Returns:
            Fear score between 0 and 100.
        """
        if not articles:
            return 50.0  # Neutral if no articles

        total_negative = 0.0
        total_articles = len(articles)

        for article in articles:
            text = f"{article.get('title', '')} {article.get('description', '')}"
            sentiment = self.compute_sentiment_score(text)

            # Count articles with negative sentiment
            if sentiment < 0:
                # Scale negative sentiment to contribution
                total_negative += abs(sentiment)

        # Normalize: higher negative sentiment = higher fear
        average_negative = total_negative / total_articles if total_articles > 0 else 0
        fear_score = min(100.0, average_negative * 100)

        return round(fear_score, 2)

    def detect_spike(self, current_fear: float) -> Optional[str]:
        """Detect fear or greed spikes based on recent history.

        Args:
            current_fear: Current fear score.

        Returns:
            'FEAR_SPIKE', 'GREED_SPIKE', or None.
        """
        if len(self.fear_history) < 4:
            return None

        # Get fear score from ~1 hour ago (4 intervals of 15 min)
        historical_scores = list(self.fear_history)
        if len(historical_scores) >= 4:
            past_fear = historical_scores[-4]
        else:
            past_fear = historical_scores[0]

        spike_increase = current_fear - past_fear

        if spike_increase >= self.spike_increase and current_fear > self.fear_threshold:
            return "FEAR_SPIKE"
        elif past_fear - current_fear >= self.spike_increase and current_fear < self.greed_threshold:
            return "GREED_SPIKE"

        return None

    def update_fear_score(self) -> Dict:
        """Main method: fetch news, compute fear score, detect spikes.

        Returns:
            Dict with fear score, detected spike, and headlines.
        """
        articles = self.fetch_news()
        self.latest_headlines = articles

        fear_score = self.compute_fear_score(articles)
        self.fear_history.append(fear_score)

        # Track if this is a new spike event
        spike_event = self.detect_spike(fear_score)

        # Check for volume anomaly keywords in headlines
        volume_anomaly = self._check_volume_keywords(articles)

        result = {
            "fear_score": fear_score,
            "spike_event": spike_event,
            "volume_anomaly": volume_anomaly,
            "headline_count": len(articles),
            "latest_headlines": [
                {
                    "title": a.get("title", ""),
                    "published_at": a.get("published_at", ""),
                    "sentiment": round(self.compute_sentiment_score(
                        f"{a.get('title', '')} {a.get('description', '')}"
                    ), 2),
                }
                for a in articles[:5]  # Top 5
            ],
        }

        if spike_event:
            logger.warning(
                "SPIKE DETECTED: %s (fear=%.2f, past=%.2f)",
                spike_event, fear_score,
                list(self.fear_history)[-4] if len(self.fear_history) >= 4 else 0,
            )
        else:
            logger.info(
                "Fear score: %.2f (history size: %d)",
                fear_score, len(self.fear_history),
            )

        self.last_fear_score = fear_score
        return result

    def _check_volume_keywords(self, articles: List[Dict]) -> bool:
        """Check if any headlines contain volume-related keywords.

        Args:
            articles: List of article dicts.

        Returns:
            True if volume anomaly keywords are found.
        """
        volume_keywords = ["volume", "unusual", "surge", "spike", "record", "massive"]

        for article in articles:
            text = (article.get("title", "") + " " + article.get("description", "")).lower()
            for keyword in volume_keywords:
                if keyword in text:
                    return True
        return False

    def _generate_simulated_headlines(self, count: int = 10) -> List[Dict]:
        """Generate simulated news headlines for testing.

        Args:
            count: Number of simulated articles.

        Returns:
            List of simulated article dicts.
        """
        import random

        sample_headlines = [
            {"title": "Crypto markets plunge as regulatory fears mount",
             "description": "Bitcoin and altcoins dump as investors sell off amid renewed regulatory concerns from major economies.",
             "source": "CryptoNews", "published_at": datetime.utcnow().isoformat()},
            {"title": "Fed signals continued rate hikes despite market turmoil",
             "description": "Federal Reserve officials indicate more tightening ahead as inflation remains persistent.",
             "source": "FinancialTimes", "published_at": datetime.utcnow().isoformat()},
            {"title": "Major exchange hack drains $200 million in user funds",
             "description": "A sophisticated attack on a prominent crypto exchange has resulted in significant losses.",
             "source": "BlockchainAlert", "published_at": datetime.utcnow().isoformat()},
            {"title": "Bitcoin rallies past key resistance level on institutional buying",
             "description": "BTC surges as institutional investors increase positions ahead of potential ETF approval.",
             "source": "CoinDesk", "published_at": datetime.utcnow().isoformat()},
            {"title": "Oil prices surge following Middle East tensions",
             "description": "Crude oil jumps as geopolitical conflicts escalate in key production regions.",
             "source": "Reuters", "published_at": datetime.utcnow().isoformat()},
        ]

        return [
            {**random.choice(sample_headlines), "published_at": datetime.utcnow().isoformat()}
            for _ in range(min(count, len(sample_headlines)))
        ]

    def get_current_fear_score(self) -> Optional[float]:
        """Get the most recently computed fear score.

        Returns:
            Latest fear score, or None if never computed.
        """
        return self.last_fear_score

    def get_fear_history(self) -> List[float]:
        """Get the full fear score history.

        Returns:
            List of fear scores in chronological order.
        """
        return list(self.fear_history)

    def check_morning_spike(self) -> Optional[Dict]:
        """Detect morning news spikes for tiered breakout trading.

        Runs 30 min before and after US market open (9:00-10:30 AM ET).
        Scrapes each source in config/news_sources.yaml.
        Counts red/orange/yellow events.
        If >=3 red events in 10 minutes, emits a MORNING_SPIKE event.

        Returns:
            Dict with spike info if triggered, None otherwise.
        """
        from datetime import datetime, timedelta, timezone
        import yaml
        import json

        try:
            # Load news sources config
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "..", "config", "news_sources.yaml"
            )
            with open(config_path, "r") as f:
                news_config = yaml.safe_load(f)
        except Exception:
            logger.warning("Could not load news_sources.yaml, using defaults")
            news_config = {
                "sources": {},
                "scan_window_minutes": 60,
                "market_open_et": "09:30",
                "spike_threshold": 3
            }

        # Check if we're in the scan window (9:00-10:30 AM ET)
        et_tz = timezone(timedelta(hours=-5))  # EST (simplified, doesn't handle DST)
        now_et = datetime.now(et_tz)
        market_open = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now_et.replace(hour=10, minute=30, second=0, microsecond=0)

        if not (market_open - timedelta(minutes=30) <= now_et <= market_close):
            return None

        # Collect events from all sources
        all_events = []
        sources = news_config.get("sources", {})

        for source_name, source_config in sources.items():
            try:
                url = source_config.get("url", "")
                source_type = source_config.get("type", "rss")

                if source_type == "rss":
                    # Simulate RSS feed parsing
                    events = self._fetch_rss_events(url, source_config)
                elif source_type == "scrape":
                    # Simulate scraping
                    events = self._fetch_scraped_events(url, source_config)
                else:
                    continue

                for event in events:
                    event["source"] = source_name
                    all_events.append(event)
            except Exception:
                logger.warning(f"Failed to fetch events from {source_name}")
                continue

        # Analyze events for severity spikes
        if not all_events:
            return None

        # Classify events by severity
        red_events = []
        orange_events = []
        yellow_events = []

        for event in all_events:
            title = event.get("title", "").lower()
            description = event.get("description", "").lower()
            combined = title + " " + description

            if any(kw in combined for kw in ["crash", "plunge", "collapse", "panic",
                                               "war", "attack", "hack", "ban", "crackdown",
                                               "surge", "escalat", "alert"]):
                red_events.append(event)
            elif any(kw in combined for kw in ["rise", "gain", "increase", "rally",
                                                 "up", "higher", "strong"]):
                orange_events.append(event)
            else:
                yellow_events.append(event)

        # Check if threshold met
        spike_threshold = news_config.get("spike_threshold", 3)
        if len(red_events) >= spike_threshold:
            # Determine most volatile pair (simplified)
            volatile_pairs = ["BTC/USD", "ETH/USD", "SOL/USD"]
            most_volatile = volatile_pairs[0] if red_events else None

            spike_event = {
                "type": "MORNING_SPIKE",
                "timestamp": now_et.isoformat(),
                "red_events_count": len(red_events),
                "orange_events_count": len(orange_events),
                "yellow_events_count": len(yellow_events),
                "most_volatile_pair": most_volatile,
                "red_event_titles": [e.get("title", "") for e in red_events[:5]],
                "action_required": True
            }

            logger.warning(
                "MORNING_SPIKE DETECTED: %d red events (threshold: %d)",
                len(red_events), spike_threshold
            )
            return spike_event

        return None

    def _fetch_rss_events(self, url: str, config: Dict) -> List[Dict]:
        """Fetch events from an RSS feed (simulated for testing)."""
        # In production, this would use feedparser or similar
        return self._simulate_events_from_source("rss", config.get("severity_selector"))

    def _fetch_scraped_events(self, url: str, config: Dict) -> List[Dict]:
        """Fetch events by scraping (simulated for testing)."""
        # In production, this would use requests + BeautifulSoup
        return self._simulate_events_from_source("scrape", config.get("severity_selector"))

    def _simulate_events_from_source(self, source_type: str, severity_selector: str) -> List[Dict]:
        """Generate simulated events for testing."""
        # This is a placeholder - in production, real HTTP requests would be made
        return []
