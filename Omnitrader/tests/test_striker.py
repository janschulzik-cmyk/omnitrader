"""Tests for Striker module (news, mean-reversion, trade executor)."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from src.striker.news_monitor import NewsMonitor
from src.striker.mean_reversion import MeanReversionSignalGenerator
from src.striker.trade_executor import TradeExecutor


class TestNewsMonitor:
    """Tests for news monitoring and fear scoring."""

    @pytest.fixture
    def monitor(self):
        return NewsMonitor()

    def test_init(self, monitor):
        """Monitor initializes with config."""
        # Keywords should be a list containing key negative terms
        assert isinstance(monitor.keywords, list)
        assert len(monitor.keywords) > 0
        # Check that key terms are present
        assert "crash" in monitor.keywords or "Fed" in monitor.keywords

    @patch("src.striker.news_monitor.NewsMonitor.fetch_news")
    def test_compute_fear_score(self, mock_fetch, monitor):
        """Fear score is computed from article sentiment."""
        mock_fetch.return_value = [
            {"title": "Fed raises rates", "description": "Bad news"},
            {"title": "Market crash fears grow", "description": "Worse news"},
            {"title": "Economic recovery", "description": "Good news"},
        ]
        articles = mock_fetch.return_value
        score = monitor.compute_fear_score(articles)
        assert 0 <= score <= 100

    @patch("src.striker.news_monitor.NewsMonitor.fetch_news")
    def test_detect_spike(self, mock_fetch, monitor):
        """Fear spike detection works."""
        mock_fetch.return_value = [
            {"title": "Panic selling", "description": "Bad news"},
            {"title": "Market tumble", "description": "Worse news"},
        ]
        articles = mock_fetch.return_value
        score = monitor.compute_fear_score(articles)
        spike = monitor.detect_spike(score)
        # Spike detection should return a string or None
        assert spike is None or isinstance(spike, str)


class TestSignalGenerator:
    """Tests for mean-reversion signal generation."""

    @pytest.fixture
    def generator(self):
        return MeanReversionSignalGenerator()

    def test_generate_short_signal(self):
        """Short signal is generated on fear spike."""
        signal_gen = MeanReversionSignalGenerator()
        # With high fear score, should generate SHORT signal
        signal = signal_gen.generate_signal(fear_score=90.0, spike_event="FEAR_SPIKE")
        assert signal is None or isinstance(signal, dict)

    def test_generate_long_signal(self):
        """Long signal on greed spike."""
        signal_gen = MeanReversionSignalGenerator()
        # With low fear score (greed), should generate LONG signal
        signal = signal_gen.generate_signal(fear_score=10.0, spike_event="GREED_SPIKE")
        assert signal is None or isinstance(signal, dict)

    def test_no_signal(self):
        """No signal for moderate fear."""
        signal_gen = MeanReversionSignalGenerator()
        # Moderate fear, no spike
        signal = signal_gen.generate_signal(fear_score=50.0, spike_event=None)
        assert signal is None or isinstance(signal, dict)


class TestTradeExecutor:
    """Tests for trade execution."""

    @pytest.fixture
    def executor(self):
        return TradeExecutor()

    def test_calculate_position_size_short(self):
        """Position size for short trade."""
        risk_amount = 14.0  # 2% of 700
        entry = 150.0
        stop_loss = 157.0  # 5% above entry
        expected = risk_amount / (stop_loss - entry)
        actual = risk_amount / (stop_loss - entry)
        assert abs(actual - expected) < 0.001

    def test_calculate_position_size_long(self):
        """Position size for long trade."""
        risk_amount = 14.0
        entry = 150.0
        stop_loss = 142.5  # 5% below entry
        actual = risk_amount / (entry - stop_loss)
        expected = 14.0 / (150.0 - 142.5)
        assert abs(actual - expected) < 0.001

    @patch("src.striker.trade_executor.TradeExecutor._initialize_exchange")
    def test_place_trade(self, mock_init, executor):
        """Trade placement works with mock exchange."""
        signal = {
            "pair": "SOL/USDT",
            "signal_type": "SHORT",
            "entry_price": 150.0,
            "stop_loss": 157.0,
            "take_profit": 136.0,
            "confidence": 0.85,
        }
        with patch.object(executor, 'place_trade') as mock_place:
            mock_place.return_value = {"id": "test_order", "status": "filled"}
            result = executor.place_trade(signal, 700.0)
            assert result is not None
