"""Tests for Morning News Spike Scalping & Tiered Futures Breakout."""

import os
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

# OMNITRADER_ROOT = parent of tests/ = /home/joe/ouroboros/cathedral/Omnitrader/
OMNITRADER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_SOURCES_PATH = os.path.join(
    OMNITRADER_ROOT, "config", "news_sources.yaml"
)
SKILL_PATH = os.path.join(
    OMNITRADER_ROOT, "config", "skills", "striker_futures_skill.txt"
)


def _et_time(hour, minute=0, second=0):
    """Create an ET timezone-aware datetime."""
    return datetime(2026, 5, 2, hour, minute, second, tzinfo=timezone(timedelta(hours=-5)))


class TestMorningSpikeDetection:
    """Test MORNING_SPIKE detection in NewsMonitor."""

    @staticmethod
    def _get_mock_events(red_count=3, orange_count=0, yellow_count=0):
        """Create mock news events."""
        events = []
        for i in range(red_count):
            events.append({
                "title": f"Market crash: major index plunges",
                "description": f"Crash alert: panic selling across markets",
                "source": "test_source",
            })
        for i in range(orange_count):
            events.append({
                "title": f"Market rise on positive data",
                "description": f"Stocks gain on strong economic report",
                "source": "test_source",
            })
        for i in range(yellow_count):
            events.append({
                "title": f"Neutral market update",
                "description": f"Markets trade flat",
                "source": "test_source",
            })
        return events

    def test_spike_triggered_with_sufficient_red_events(self):
        """Spike triggers when >=3 red events in scan window."""
        from src.striker.news_monitor import NewsMonitor

        monitor = NewsMonitor()
        red_events = self._get_mock_events(red_count=4)

        with patch.object(monitor, "_fetch_rss_events", return_value=red_events):
            with patch.object(monitor, "_fetch_scraped_events", return_value=[]):
                fake_time = _et_time(9, 30)
                with patch("datetime.datetime") as mock_dt:
                    mock_dt.now.return_value = fake_time
                    mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                    result = monitor.check_morning_spike()

        assert result is not None
        assert result["type"] == "MORNING_SPIKE"
        assert result["red_events_count"] == 4
        assert result["action_required"] is True

    def test_no_spike_with_insufficient_red_events(self):
        """No spike when <3 red events."""
        from src.striker.news_monitor import NewsMonitor

        monitor = NewsMonitor()
        red_events = self._get_mock_events(red_count=2)

        with patch.object(monitor, "_fetch_rss_events", return_value=red_events):
            with patch.object(monitor, "_fetch_scraped_events", return_value=[]):
                fake_time = _et_time(9, 30)
                with patch("datetime.datetime") as mock_dt:
                    mock_dt.now.return_value = fake_time
                    mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                    result = monitor.check_morning_spike()

        assert result is None

    def test_spike_threshold_configurable(self):
        """Spike threshold can be configured via news_sources.yaml."""
        from src.striker.news_monitor import NewsMonitor
        import yaml

        # Build a YAML string with spike_threshold=2 and a test source
        test_config = {
            "sources": {
                "test_source": {
                    "url": "http://test",
                    "type": "rss",
                }
            },
            "market_open_et": "09:30",
            "spike_threshold": 2,
        }
        yaml_str = yaml.dump(test_config)

        monitor = NewsMonitor()
        red_events = self._get_mock_events(red_count=2)

        # Patch builtins.open to return our test config
        with patch('builtins.open', return_value=StringIO(yaml_str)):
            with patch.object(monitor, '_fetch_rss_events', return_value=red_events):
                with patch.object(monitor, '_fetch_scraped_events', return_value=[]):
                    fake_time = _et_time(9, 30)
                    with patch("datetime.datetime") as mock_dt:
                        mock_dt.now.return_value = fake_time
                        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                        result = monitor.check_morning_spike()

        assert result is not None
        assert result["red_events_count"] == 2

    def test_outside_scan_window_returns_none(self):
        """No spike detected outside 9:00-10:30 AM ET window."""
        from src.striker.news_monitor import NewsMonitor

        monitor = NewsMonitor()
        red_events = self._get_mock_events(red_count=5)

        with patch.object(monitor, "_fetch_rss_events", return_value=red_events):
            # Time outside window: 2:00 PM ET
            fake_time = _et_time(14, 0)
            with patch("datetime.datetime") as mock_dt:
                mock_dt.now.return_value = fake_time
                mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
                result = monitor.check_morning_spike()

        assert result is None


class TestTieredBreakoutLogic:
    """Test tiered entry/stop logic for futures breakout."""

    def test_tier1_entry(self):
        """Tier 1: Enter with 0.5% of pool."""
        from src.striker.trade_executor import TradeExecutor

        executor = TradeExecutor(market_type="futures")
        pool_size = 100000.0

        position_size = executor.calculate_futures_position_size(
            pool_size=pool_size, risk_per_trade=0.005, leverage=1
        )
        assert position_size == 500.0

    def test_tier2_entry_after_tp1(self):
        """Tier 2: Add position after TP1 hit."""
        from src.striker.trade_executor import TradeExecutor

        executor = TradeExecutor(market_type="futures")
        pool_size = 100000.0

        tier1_size = executor.calculate_futures_position_size(
            pool_size=pool_size, risk_per_trade=0.005, leverage=1
        )
        tier2_size = executor.calculate_futures_position_size(
            pool_size=pool_size, risk_per_trade=0.005, leverage=1
        )
        assert tier1_size + tier2_size == 1000.0

    def test_tier3_entry_if_trend_continues(self):
        """Tier 3: Repeat once more if trend continues."""
        from src.striker.trade_executor import TradeExecutor

        executor = TradeExecutor(market_type="futures")
        pool_size = 100000.0
        total_risk = executor.calculate_futures_position_size(
            pool_size, 0.005, 1
        ) * 3
        assert total_risk == 1500.0

    def test_total_risk_never_exceeds_0_5_percent_per_tier(self):
        """Each tier's risk is capped at 0.5% of pool."""
        from src.striker.trade_executor import TradeExecutor

        executor = TradeExecutor(market_type="futures")
        pool_sizes = [10000, 50000, 100000, 500000]

        for pool_size in pool_sizes:
            tier_risk = executor.calculate_futures_position_size(
                pool_size=pool_size, risk_per_trade=0.005, leverage=1
            )
            expected = pool_size * 0.005
            assert abs(tier_risk - expected) < 0.01

    def test_leverage_amplifies_position_size(self):
        """Higher leverage increases position size proportionally."""
        from src.striker.trade_executor import TradeExecutor

        executor = TradeExecutor(market_type="futures")
        pool_size = 100000.0
        size_1x = executor.calculate_futures_position_size(
            pool_size, risk_per_trade=0.005, leverage=1
        )
        size_10x = executor.calculate_futures_position_size(
            pool_size, risk_per_trade=0.005, leverage=10
        )
        assert size_10x == size_1x * 10


class TestFuturesMarketType:
    """Test futures vs spot market type distinction."""

    def test_default_is_spot(self):
        """Default market_type is 'spot'."""
        from src.striker.trade_executor import TradeExecutor

        executor = TradeExecutor()
        assert executor.market_type == "spot"

    def test_futures_market_type(self):
        """Futures market_type sets correctly."""
        from src.striker.trade_executor import TradeExecutor

        executor = TradeExecutor(market_type="futures")
        assert executor.market_type == "futures"

    def test_load_with_futures(self):
        """Singleton load respects market_type parameter."""
        from src.striker.trade_executor import TradeExecutor

        TradeExecutor._instance = None
        executor = TradeExecutor.load(market_type="futures")
        assert executor.market_type == "futures"


class TestNewsSourcesConfig:
    """Test news_sources.yaml config loading."""

    def test_config_file_exists(self):
        """news_sources.yaml is present in config/."""
        assert os.path.exists(NEWS_SOURCES_PATH)

    def test_config_has_required_keys(self):
        """Config has sources, scan_window_minutes, market_open_et, spike_threshold."""
        import yaml

        with open(NEWS_SOURCES_PATH, "r") as f:
            config = yaml.safe_load(f)

        assert "sources" in config
        assert "scan_window_minutes" in config
        assert "market_open_et" in config
        assert "spike_threshold" in config

    def test_skill_file_exists(self):
        """striker_futures_skill.txt is present."""
        assert os.path.exists(SKILL_PATH)

    def test_skill_file_has_breakout_rules(self):
        """Skill file contains tiered breakout rules."""
        with open(SKILL_PATH, "r") as f:
            content = f.read()

        assert "Tier 1" in content
        assert "Tier 2" in content
        assert "Tier 3" in content
        assert "0.5%" in content
        assert "trailing stop" in content.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
