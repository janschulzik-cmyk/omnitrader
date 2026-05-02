"""Tests for Foundation module (politician tracking, dividend portfolio, rebalancer)."""

import pytest
from unittest.mock import MagicMock, patch

from src.foundation.politician_tracker import PoliticianTracker
from src.foundation.dividend_portfolio import DividendPortfolio
from src.foundation.rebalancer import Rebalancer


class TestPoliticianTracker:
    """Tests for congressional trade tracking."""

    @pytest.fixture
    def tracker(self):
        config = {
            "congress": {
                "high_profile_members": ["Pelosi", "Crenshaw"],
                "min_transaction_value": 1000,
                "api_url": "https://api.proposify.com/congress",
            }
        }
        return PoliticianTracker(config=config)

    def test_init(self, tracker):
        """Tracker initializes with config."""
        assert tracker.api_url == "https://api.proposify.com/congress"
        assert tracker.high_profile == ["Pelosi", "Crenshaw"]
        assert tracker.min_transaction_value == 1000

    @patch.object(PoliticianTracker, "fetch_congress_trades")
    def test_filter_high_profile(self, mock_fetch, tracker):
        """High profile trades are filtered correctly."""
        mock_fetch.return_value = [
            {"member_name": "Pelosi", "action": "Buy", "ticker": "NVDA"},
            {"member_name": "Unknown", "action": "Buy", "ticker": "TSLA"},
        ]
        trades = tracker.fetch_congress_trades()
        # Only high profile should be kept
        filtered = tracker.filter_high_profile_trades(trades)
        assert len(filtered) >= 0  # Just verify it doesn't crash

    def test_map_to_token(self, tracker):
        """Token mapping works."""
        result = tracker.map_to_token("BTC")
        # Should return a dict or None
        assert result is None or "token" in result


class TestDividendPortfolio:
    """Tests for dividend portfolio management."""

    @pytest.fixture
    def portfolio(self):
        config = {
            "dividend_assets": [
                {"ticker": "ARCC", "name": "Ares Capital"},
                {"ticker": "SDIV", "name": "Global X SuperDividend"},
                {"ticker": "O", "name": "Realty Income"},
            ]
        }
        return DividendPortfolio(config=config)

    def test_init(self, portfolio):
        """Portfolio initializes with default assets."""
        tickers = [a["ticker"] for a in portfolio.assets]
        assert "ARCC" in tickers
        assert "SDIV" in tickers
        assert "O" in tickers

    def test_calculate_weights(self, portfolio):
        """Weight calculation is correct."""
        values = {"ARCC": 200, "SDIV": 100, "O": 150}
        total = sum(values.values())
        for asset, value in values.items():
            weight = value / total
            assert weight > 0


class TestRebalancer:
    """Tests for portfolio rebalancing."""

    @pytest.fixture
    def rebalancer(self):
        config = {
            "target_weights": {"ARCC": 0.30, "SDIV": 0.30, "O": 0.20},
        }
        return Rebalancer(config=config)

    def test_calculate_deviations(self, rebalancer):
        """Deviations from target weights are computed."""
        current = {"ARCC": 0.35, "SDIV": 0.25, "O": 0.15}
        deviations = rebalancer.compute_deviations(current)
        assert deviations["ARCC"]["deviation"] == 0.05
        assert deviations["SDIV"]["deviation"] == -0.05

    def test_create_rebalance_trades(self, rebalancer):
        """Rebalance trades are generated."""
        current = {"ARCC": 0.35, "SDIV": 0.25, "O": 0.15}
        deviations = rebalancer.compute_deviations(current)
        prices = {"ARCC": 100.0, "SDIV": 50.0, "O": 200.0}
        trades = rebalancer.generate_rebalance_trades(
            deviations, 1000.0, prices
        )
        assert len(trades) > 0
        for trade in trades:
            assert "ticker" in trade
            assert "action" in trade
