"""Offline backtesting engine for Striker module.

Replays historical OHLCV data from CSV files, emitting synthetic
FEAR_SPIKE and MORNING_SPIKE events for testing the trading pipeline
without any live market data.
"""

import os
import csv
import json
import time
import math
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger("backtesting.engine")

# ── Data Models ──────────────────────────────────────────────────

@dataclass
class Candle:
    """OHLCV candle from historical data."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_shadow(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def change_pct(self) -> float:
        if self.open == 0:
            return 0.0
        return (self.close - self.open) / self.open * 100


@dataclass
class BacktestResult:
    """Results from a backtest run."""
    pair: str
    candles_count: int
    start_time: str
    end_time: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    num_signals: int
    num_trades: int
    winning_trades: int
    losing_trades: int
    max_drawdown_pct: float
    avg_trade_return_pct: float
    signals: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)


# ── Backtest Engine ─────────────────────────────────────────────

class BacktestEngine:
    """Replays OHLCV data and simulates Striker trading logic."""

    def __init__(self, config: Dict = None):
        """Initialize the backtest engine.

        Args:
            config: Configuration dict with:
                - fear_threshold: Fear score threshold for SHORT signals (default 70)
                - greed_threshold: Fear score threshold for LONG signals (default 30)
                - volatility_threshold: Min candle range % to trigger analysis (default 2.0)
                - max_position_size: Max position size as fraction of capital (default 0.1)
                - risk_per_trade: Risk per trade as fraction of capital (default 0.02)
        """
        self.config = config or {}
        self.fear_threshold = self.config.get("fear_threshold", 70)
        self.greed_threshold = self.config.get("greed_threshold", 30)
        self.volatility_threshold = self.config.get("volatility_threshold", 2.0)
        self.max_position_size = self.config.get("max_position_size", 0.1)
        self.risk_per_trade = self.config.get("risk_per_trade", 0.02)

        # State tracking
        self.capital = 10000.0  # Start with $10K
        self.position = None  # Current open position
        self.trades = []  # Closed trades
        self.signals = []  # All signals generated
        self.equity_curve = []  # Equity at each candle

    def load_ohlcv(self, csv_path: str) -> List[Candle]:
        """Load OHLCV data from a CSV file.

        Expected CSV columns: timestamp, open, high, low, close, volume
        Timestamp can be Unix epoch (seconds) or ISO format.

        Returns:
            List of Candle objects sorted chronologically.
        """
        candles = []
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                try:
                    ts = row.get("timestamp", "0")
                    # Handle different timestamp formats
                    try:
                        ts = float(ts)
                        if ts > 1e12:
                            # Millisecond epoch
                            ts = int(ts) // 1000
                        else:
                            ts = int(ts)
                    except ValueError:
                        # Try parsing ISO format
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        ts = int(dt.timestamp())

                    candle = Candle(
                        timestamp=ts,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row.get("volume", 0)),
                    )
                    candles.append(candle)
                except (ValueError, KeyError) as e:
                    logger.warning("Skipping bad row: %s", e)

        # Sort chronologically
        candles.sort(key=lambda c: c.timestamp)
        logger.info("Loaded %d candles from %s", len(candles), csv_path)
        return candles

    def generate_fear_score(self, candle: Candle, prev_candles: List[Candle]) -> float:
        """Generate a synthetic fear/greed score based on price action.

        This simulates what the real Striker would calculate from
        market data, news sentiment, and on-chain metrics.

        Returns a score from 0 (greed) to 100 (fear).
        """
        score = 50.0  # Neutral baseline

        # 1. Price change penalty/bonus
        change_pct = candle.change_pct
        if change_pct < -5:
            score += 30  # Major drop = high fear
        elif change_pct < -2:
            score += 20  # Moderate drop
        elif change_pct < -1:
            score += 10  # Small drop
        elif change_pct > 5:
            score -= 30  # Major pump = greed
        elif change_pct > 2:
            score -= 20  # Moderate pump
        elif change_pct > 1:
            score -= 10  # Small pump

        # 2. Volatility component
        if candle.range > 0 and candle.open > 0:
            range_pct = candle.range / candle.open * 100
            if range_pct > 10:
                score += 15  # High volatility = uncertainty
            elif range_pct > 5:
                score += 8

        # 3. Candle pattern component
        if candle.close < candle.open:  # Bearish candle
            # Long lower shadow = rejection (bullish)
            if candle.lower_shadow > candle.body * 2 and candle.body > 0:
                score -= 5
            # Long upper shadow = rejection (bearish)
            elif candle.upper_shadow > candle.body * 2 and candle.body > 0:
                score += 5
            # Doji (small body)
            elif candle.body < candle.range * 0.1:
                score += 3  # Indecision
        else:  # Bullish candle
            if candle.upper_shadow > candle.body * 2 and candle.body > 0:
                score += 5  # Rejection of highs

        # 4. Volume surge = conviction
        if prev_candles and prev_candles[-1].volume > 0:
            vol_ratio = candle.volume / prev_candles[-1].volume
            if vol_ratio > 3 and change_pct < 0:
                score += 10  # High volume drop = panic selling
            elif vol_ratio > 3 and change_pct > 0:
                score -= 5  # High volume pump = FOMO

        # 5. Multi-candle trend
        if len(prev_candles) >= 3:
            recent_closes = [c.close for c in prev_candles[-3:]] + [candle.close]
            if all(recent_closes[i] >= recent_closes[i+1] for i in range(len(recent_closes)-1)):
                score += 15  # Strong downtrend
            elif all(recent_closes[i] <= recent_closes[i+1] for i in range(len(recent_closes)-1)):
                score -= 15  # Strong uptrend

        return max(0, min(100, score))

    def check_signal(self, candle: Candle, prev_candles: List[Candle]) -> Optional[Dict]:
        """Check if a trading signal should be generated.

        Returns signal dict if triggered, None otherwise.
        """
        fear_score = self.generate_fear_score(candle, prev_candles)

        signal = None

        if fear_score >= self.fear_threshold and self.position is None:
            # SHORT signal from fear
            signal = {
                "type": "FEAR_SPIKE",
                "signal_type": "SHORT",
                "fear_score": fear_score,
                "entry_price": candle.close,
                "pair": "SOL/USDT",
                "timestamp": candle.timestamp,
                "confidence": min(fear_score / 100, 1.0),
                "trigger": f"fear_score={fear_score:.1f} >= threshold={self.fear_threshold}",
            }
        elif fear_score <= self.greed_threshold and self.position is None:
            # LONG signal from greed
            signal = {
                "type": "MORNING_SPIKE",
                "signal_type": "LONG",
                "fear_score": fear_score,
                "entry_price": candle.close,
                "pair": "SOL/USDT",
                "timestamp": candle.timestamp,
                "confidence": min((100 - fear_score) / 100, 1.0),
                "trigger": f"fear_score={fear_score:.1f} <= threshold={self.greed_threshold}",
            }

        if signal:
            self.signals.append(signal)
            return signal

        return None

    def execute_signal(self, signal: Dict, candle: Candle) -> Optional[Dict]:
        """Execute a signal: open position with risk management."""
        if self.position:
            return None  # Already in a position

        price = candle.close
        side = signal["signal_type"]

        # Calculate position size based on risk
        # Risk per trade = distance to stop loss * quantity
        stop_pct = 0.05  # 5% stop loss
        risk_amount = self.capital * self.risk_per_trade
        stop_price = price * (1 + stop_pct) if side == "SHORT" else price * (1 - stop_pct)

        quantity = risk_amount / (price * stop_pct) if stop_pct > 0 else 0
        max_qty = self.capital * self.max_position_size / price
        quantity = min(quantity, max_qty)

        if quantity <= 0:
            return None

        position = {
            "signal": signal,
            "side": side,
            "entry_price": price,
            "quantity": quantity,
            "stop_loss": stop_price,
            "take_profit": price * (1 - stop_pct * 2) if side == "SHORT" else price * (1 + stop_pct * 2),
            "entry_time": candle.timestamp,
            "capital_at_entry": self.capital,
        }
        self.position = position
        return position

    def check_exit(self, candle: Candle) -> Optional[Dict]:
        """Check if an open position should be closed."""
        if not self.position:
            return None

        pos = self.position
        price = candle.close
        close_reason = None
        exit_price = price

        if pos["side"] == "SHORT":
            if price >= pos["stop_loss"]:
                close_reason = "stop_loss"
                exit_price = pos["stop_loss"]
            elif price <= pos["take_profit"]:
                close_reason = "take_profit"
                exit_price = pos["take_profit"]
        else:  # LONG
            if price <= pos["stop_loss"]:
                close_reason = "stop_loss"
                exit_price = pos["stop_loss"]
            elif price >= pos["take_profit"]:
                close_reason = "take_profit"
                exit_price = pos["take_profit"]

        if close_reason:
            pnl = self._calculate_pnl(pos, exit_price)
            trade = {
                "signal": pos["signal"],
                "side": pos["side"],
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "quantity": pos["quantity"],
                "pnl": pnl,
                "pnl_pct": (pnl / pos["capital_at_entry"]) * 100,
                "close_reason": close_reason,
                "entry_time": pos["entry_time"],
                "exit_time": candle.timestamp,
                "duration_minutes": (candle.timestamp - pos["entry_time"]) / 60,
            }
            self.trades.append(trade)
            self.capital += pnl
            self.position = None
            return trade

        return None

    def _calculate_pnl(self, position: Dict, exit_price: float) -> float:
        """Calculate PnL for a closed position."""
        qty = position["quantity"]
        entry = position["entry_price"]
        if position["side"] == "SHORT":
            return (entry - exit_price) * qty
        else:
            return (exit_price - entry) * qty

    def run(self, csv_path: str, pair: str = "SOL/USDT") -> BacktestResult:
        """Run a complete backtest.

        Args:
            csv_path: Path to OHLCV CSV file.
            pair: Trading pair name (for reporting).

        Returns:
            BacktestResult with all metrics.
        """
        candles = self.load_ohlcv(csv_path)
        if not candles:
            logger.error("No candles loaded from %s", csv_path)
            return self._empty_result(pair)

        prev_candles = []

        for i, candle in enumerate(candles):
            # Check exit first (if position open)
            trade = self.check_exit(candle)

            # Generate equity point
            current_equity = self.capital
            if self.position:
                current_equity += self._calculate_pnl(self.position, candle.close)
            self.equity_curve.append({
                "timestamp": candle.timestamp,
                "equity": current_equity,
                "candle": i,
            })

            # Track max drawdown
            if self.equity_curve:
                peak = max(e["equity"] for e in self.equity_curve)
                dd = (peak - current_equity) / peak * 100 if peak > 0 else 0
                if not hasattr(self, "_max_drawdown") or dd > self._max_drawdown:
                    self._max_drawdown = dd

            # Check new signal
            if not self.position:
                signal = self.check_signal(candle, prev_candles)
                if signal:
                    self.execute_signal(signal, candle)

            prev_candles.append(candle)
            # Keep only last 10 for analysis
            if len(prev_candles) > 10:
                prev_candles = prev_candles[-10:]

        return self._build_result(pair, candles)

    def _empty_result(self, pair: str) -> BacktestResult:
        """Return an empty result for when no data is available."""
        return BacktestResult(
            pair=pair,
            candles_count=0,
            start_time="",
            end_time="",
            initial_capital=self.capital,
            final_capital=self.capital,
            total_return_pct=0.0,
            num_signals=0,
            num_trades=0,
            winning_trades=0,
            losing_trades=0,
            max_drawdown_pct=0.0,
            avg_trade_return_pct=0.0,
        )

    def _build_result(self, pair: str, candles: List[Candle]) -> BacktestResult:
        """Build the final BacktestResult."""
        if self.trades:
            wins = sum(1 for t in self.trades if t["pnl"] > 0)
            losses = len(self.trades) - wins
            avg_pnl = sum(t["pnl_pct"] for t in self.trades) / len(self.trades)
        else:
            wins = 0
            losses = 0
            avg_pnl = 0.0

        return BacktestResult(
            pair=pair,
            candles_count=len(candles),
            start_time=datetime.fromtimestamp(candles[0].timestamp, tz=timezone.utc).isoformat(),
            end_time=datetime.fromtimestamp(candles[-1].timestamp, tz=timezone.utc).isoformat(),
            initial_capital=10000.0,
            final_capital=self.capital,
            total_return_pct=(self.capital - 10000.0) / 10000.0 * 100,
            num_signals=len(self.signals),
            num_trades=len(self.trades),
            winning_trades=wins,
            losing_trades=losses,
            max_drawdown_pct=getattr(self, "_max_drawdown", 0.0),
            avg_trade_return_pct=avg_pnl,
            signals=[s for s in self.signals],
            trades=[{k: v for k, v in t.items() if k != "signal"} for t in self.trades],
        )


# ── CSV Data Generator ───────────────────────────────────────────

def generate_sample_ohlcv(
    output_path: str,
    pair: str = "SOL/USDT",
    days: int = 30,
    interval: str = "1h",
    start_price: float = 145.0,
    volatility: float = 0.03,
):
    """Generate synthetic OHLCV data for backtesting.

    Uses geometric Brownian motion with periodic trend shifts
    to simulate realistic price action.

    Args:
        output_path: Where to write the CSV.
        pair: Trading pair name (header only).
        days: Number of days of data to generate.
        interval: Time interval (e.g., "1h", "4h", "1d").
        start_price: Starting price.
        volatility: Daily volatility factor (higher = more volatile).
    """
    intervals = {"1h": 24, "4h": 6, "1d": 1}
    hours_per_day = intervals.get(interval, 24)
    total_candles = days * hours_per_day

    rows = [{"timestamp": "", "open": "", "high": "", "low": "", "close": "", "volume": ""}]

    price = start_price
    trend = 0.0  # Current trend bias
    trend_duration = 0  # Candles remaining in current trend

    for i in range(total_candles):
        # Shift trend periodically
        trend_duration -= 1
        if trend_duration <= 0:
            trend = (0.5 - 0.3) * volatility  # New random trend between -0.3% and +0.5%
            trend_duration = 5 + int(10 * (0.5 - 0.3))  # 5-15 candles

        # Price movement (Geometric Brownian Motion)
        dt = 1 / 24 if interval == "1d" else 1 / (24 * hours_per_day)
        mu = trend  # drift
        sigma = volatility * (dt ** 0.5)
        import random
        noise = random.gauss(mu, sigma)
        new_price = price * (1 + noise)

        # Generate OHLC from close
        close = new_price
        open_price = price
        high = max(open_price, close) * (1 + abs(random.gauss(0, volatility * 0.5)))
        low = min(open_price, close) * (1 - abs(random.gauss(0, volatility * 0.5)))
        volume = 100000 + random.randint(-50000, 200000)

        # Timestamp
        dt_obj = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        interval_secs = {"1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 3600)
        ts = dt_obj.timestamp() + i * interval_secs

        rows.append({
            "timestamp": ts,
            "open": f"{open_price:.4f}",
            "high": f"{high:.4f}",
            "low": f"{low:.4f}",
            "close": f"{close:.4f}",
            "volume": str(volume),
        })

        price = close

    # Write CSV
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows[1:])

    logger.info("Generated %d candles for %s -> %s", len(rows)-1, pair, output_path)


if __name__ == "__main__":
    # Quick test: generate data and run backtest
    sample_path = Path(__file__).parent.parent / "data" / "sample_ohlcv.csv"
    generate_sample_ohlcv(str(sample_path))

    engine = BacktestEngine({
        "fear_threshold": 70,
        "greed_threshold": 30,
        "volatility_threshold": 2.0,
        "max_position_size": 0.1,
        "risk_per_trade": 0.02,
    })
    result = engine.run(str(sample_path))

    print(f"{'='*60}")
    print(f"Backtest Results: {result.pair}")
    print(f"{'='*60}")
    print(f"Candles: {result.candles_count}")
    print(f"Period: {result.start_time} to {result.end_time}")
    print(f"Initial Capital: ${result.initial_capital:.2f}")
    print(f"Final Capital:   ${result.final_capital:.2f}")
    print(f"Total Return:    {result.total_return_pct:+.2f}%")
    print(f"Signals:         {result.num_signals}")
    print(f"Trades:          {result.num_trades} (W: {result.winning_trades}, L: {result.losing_trades})")
    print(f"Avg Trade PnL:   {result.avg_trade_return_pct:+.2f}%")
    print(f"Max Drawdown:    {result.max_drawdown_pct:.2f}%")
    print(f"{'='*60}")

    if result.trades:
        print(f"Trade Summary:")
        for t in result.trades[:10]:
            direction = "LONG" if t["side"] == "LONG" else "SHORT"
            reason = t["close_reason"].replace("_", " ")
            print(f"  {direction} ${t['entry_price']:.2f} -> ${t['exit_price']:.2f} "
                  f"PnL=${t['pnl']:+.2f} ({t['pnl_pct']:+.2f}%) [{reason}]")
        if len(result.trades) > 10:
            print(f"  ... and {len(result.trades) - 10} more trades")

