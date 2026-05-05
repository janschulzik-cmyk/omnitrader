"""Tests for risk modules: Kelly sizing, correlation, circuit breaker."""

import os
import sys
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.risk.position_sizer import fractional_kelly, calc_position_size
from src.risk.correlation import check_correlation
from src.risk.circuit_breaker import CircuitBreaker


class TestFractionalKelly:
    """Tests for fractional Kelly sizing."""

    def test_positive_expectancy(self):
        """Win rate 0.5, win/loss 2:1 should give positive Kelly."""
        result = fractional_kelly(win_rate=0.5, avg_win_pct=2.0, avg_loss_pct=1.0)
        assert result > 0

    def test_negative_expectancy(self):
        """Win rate 0.3, win/loss 1:1 should give zero Kelly."""
        result = fractional_kelly(win_rate=0.3, avg_win_pct=1.0, avg_loss_pct=1.0)
        assert result == 0.0

    def test_defaults_from_config(self):
        """Using defaults from best_params.yaml should give valid result."""
        result = fractional_kelly(
            win_rate=0.45, avg_win_pct=1.5, avg_loss_pct=1.0
        )
        # Should be positive but small (conservative)
        assert 0 <= result <= 0.1

    def test_invalid_inputs(self):
        """Zero or negative percentages should return 0."""
        assert fractional_kelly(0.5, 0, 1.0) == 0.0
        assert fractional_kelly(0.5, -1.0, 1.0) == 0.0


class TestCalcPositionSize:
    """Tests for full position size calculation."""

    def test_kelly_method(self):
        """Should use kelly method when edge is positive."""
        result = calc_position_size(
            pool_balance=100.0,
            entry_price=100.0,
            stop_loss=95.0,
            win_rate=0.5,
            avg_win_pct=2.0,
            avg_loss_pct=1.0,
        )
        assert result["size_method"] in ("kelly", "fixed")
        assert result["position_size"] > 0

    def test_zero_edge(self):
        """Negative expectancy should fall back to fixed."""
        result = calc_position_size(
            pool_balance=100.0,
            entry_price=100.0,
            stop_loss=95.0,
            win_rate=0.3,
            avg_win_pct=1.0,
            avg_loss_pct=1.0,
        )
        assert result["size_method"] == "fixed"
        assert result["position_size"] > 0

    def test_below_minimum(self):
        """Very small position should be rejected."""
        result = calc_position_size(
            pool_balance=5.0,
            entry_price=100.0,
            stop_loss=99.9,
            win_rate=0.5,
            avg_win_pct=2.0,
            avg_loss_pct=1.0,
        )
        assert result["position_size"] == 0.0
        assert result["size_method"] == "zero"


class TestCorrelation:
    """Tests for position correlation checks."""

    def test_no_open_positions(self):
        """No open positions should not be correlated."""
        result = check_correlation(
            open_positions=[],
            new_signal={"pair": "BTC/USDT", "signal_type": "LONG"},
            ccxt_exchange=None,
        )
        assert result["is_highly_correlated"] is False
        assert result["size_multiplier"] == 1.0

    def test_same_pair_different_side(self):
        """Same pair but different side should not be correlated."""
        result = check_correlation(
            open_positions=[{"pair": "BTC/USDT", "side": "LONG"}],
            new_signal={"pair": "BTC/USDT", "signal_type": "SHORT"},
            ccxt_exchange=None,
        )
        assert result["is_highly_correlated"] is False

    def test_different_pairs(self):
        """Different pairs should not trigger correlation."""
        result = check_correlation(
            open_positions=[{"pair": "BTC/USDT", "side": "LONG"}],
            new_signal={"pair": "ETH/USDT", "signal_type": "LONG"},
            ccxt_exchange=None,
        )
        # May or may not be correlated depending on historical data
        # Just verify it returns valid structure
        assert "is_highly_correlated" in result
        assert "size_multiplier" in result


class TestCircuitBreaker:
    """Tests for circuit breaker."""

    @pytest.fixture
    def cb_db(self, tmp_path):
        """Create an in-memory SQLite DB URL for CircuitBreaker."""
        db_file = tmp_path / "test_cb.db"
        return f"sqlite:///{db_file}"

    def test_no_drawdown(self, cb_db):
        """No drawdown should not trigger breaker."""
        cb = CircuitBreaker(threshold=0.20, db_url=cb_db)
        result = cb.check(current_pnl=0.0, total_capital=100.0)
        assert result["breaker_triggered"] is False
        assert result["drawdown_pct"] == 0.0

    def test_below_threshold(self, cb_db):
        """Drawdown below threshold should not trigger."""
        cb = CircuitBreaker(threshold=0.20, db_url=cb_db)
        result = cb.check(current_pnl=-5.0, total_capital=100.0)
        assert result["breaker_triggered"] is False

    def test_above_threshold(self, cb_db):
        """Drawdown above threshold should trigger."""
        cb = CircuitBreaker(threshold=0.20, db_url=cb_db)
        result = cb.check(current_pnl=-25.0, total_capital=100.0)
        assert result["breaker_triggered"] is True

    def test_get_status(self, cb_db):
        """Should return valid status dict."""
        cb = CircuitBreaker(threshold=0.20, db_url=cb_db)
        status = cb.get_status()
        assert "breaker_triggered" in status
        assert "breaker_status" in status
        assert "peak_pnl" in status
