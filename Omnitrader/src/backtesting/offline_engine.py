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
                - fear_threshold: Fear score threshold for SHORT signals (default 55)
                - greed_threshold: Fear score threshold for LONG signals (default 50)
                - volatility_threshold: Min candle range % to trigger analysis (default 2.0)
                - max_position_size: Max position size as fraction of capital (default 0.1)
                - risk_per_trade: Risk per trade as fraction of capital (default 0.02)
                - backtest: Backtest-specific parameter overrides:
                  volume_multiplier, min_candle_size_pct, require_wick_rejection
        """
        self.config = config or {}
        self.fear_threshold: float = self.config.get("fear_threshold", 55)
        self.greed_threshold: float = self.config.get("greed_threshold", 50)
        self.volatility_threshold: float = self.config.get("volatility_threshold", 2.0)
        self.max_position_size = self.config.get("max_position_size", 0.1)
        self.risk_per_trade = self.config.get("risk_per_trade", 0.02)

        # Backtest-specific parameters (Phase 1.1)
        bt_config = self.config.get("backtest", self.config)
        self.volume_multiplier = bt_config.get("volume_multiplier", 2.0)
        self.min_candle_size_pct = bt_config.get("min_candle_size_pct", 6.0)
        self.require_wick_rejection = bt_config.get("require_wick_rejection", False)

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
                    try:
                        ts = float(ts)
                        if ts > 1e12:
                            ts = int(ts) // 1000
                        else:
                            ts = int(ts)
                    except ValueError:
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
            score += 30
        elif change_pct < -2:
            score += 20
        elif change_pct < -1:
            score += 10
        elif change_pct > 5:
            score -= 30
        elif change_pct > 2:
            score -= 20
        elif change_pct > 1:
            score -= 10

        # 2. Volatility component
        if candle.range > 0 and candle.open > 0:
            range_pct = candle.range / candle.open * 100
            if range_pct > 10:
                score += 15
            elif range_pct > 5:
                score += 8

        # 3. Candle pattern component
        if candle.close < candle.open:  # Bearish candle
            if candle.lower_shadow > candle.body * 2 and candle.body > 0:
                score -= 5
            elif candle.upper_shadow > candle.body * 2 and candle.body > 0:
                score += 5
            elif candle.body < candle.range * 0.1:
                score += 3
        else:  # Bullish candle
            if candle.upper_shadow > candle.body * 2 and candle.body > 0:
                score += 5

        # 4. Volume surge = conviction
        if prev_candles and prev_candles[-1].volume > 0:
            vol_ratio = candle.volume / prev_candles[-1].volume
            if vol_ratio > 3 and change_pct < 0:
                score += 10
            elif vol_ratio > 3 and change_pct > 0:
                score -= 5

        # 5. Multi-candle trend
        if len(prev_candles) >= 3:
            recent_closes = [c.close for c in prev_candles[-3:]] + [candle.close]
            if all(recent_closes[i] >= recent_closes[i+1] for i in range(len(recent_closes)-1)):
                score += 15
            elif all(recent_closes[i] <= recent_closes[i+1] for i in range(len(recent_closes)-1)):
                score -= 15

        return max(0, min(100, score))

    def check_signal(self, candle: Candle, prev_candles: List[Candle]) -> Optional[Dict]:
        """Check if a trading signal should be generated.

        Applies backtest parameters:
        - volume_multiplier: minimum volume spike ratio to lower threshold
        - min_candle_size_pct: minimum candle body % range
        - require_wick_rejection: require long wick for signal

        Returns signal dict if triggered, None otherwise.
        """
        fear_score = self.generate_fear_score(candle, prev_candles)

        # Volume spike check
        vol_anomaly = False
        if prev_candles and prev_candles[-1].volume > 0:
            vol_ratio = candle.volume / prev_candles[-1].volume
            vol_anomaly = vol_ratio >= self.volume_multiplier

        # Min candle size check (range %)
        candle_range_pct = 0
        if candle.open > 0 and candle.range > 0:
            candle_range_pct = candle.range / candle.open * 100
        size_ok = candle_range_pct >= self.min_candle_size_pct

        # Wick rejection check
        wick_rejection_ok = True
        if self.require_wick_rejection:
            upper_wick = candle.upper_shadow
            lower_wick = candle.lower_shadow
            candle_range = candle.range
            if candle_range > 0:
                upper_wick_ratio = upper_wick / candle_range
                lower_wick_ratio = lower_wick / candle_range
            else:
                upper_wick_ratio = 0
                lower_wick_ratio = 0

            if fear_score >= 50:  # Potential SHORT
                wick_rejection_ok = upper_wick_ratio >= 0.3
            else:  # Potential LONG
                wick_rejection_ok = lower_wick_ratio >= 0.3

        # Dynamic threshold: lower bar when volume spike present
        if vol_anomaly:
            short_threshold = self.fear_threshold - 10
            long_threshold = self.greed_threshold + 10
        else:
            short_threshold = self.fear_threshold
            long_threshold = self.greed_threshold

        signal = None

        if (fear_score >= short_threshold and
                size_ok and wick_rejection_ok and self.position is None):
            signal = {
                "type": "FEAR_SPIKE",
                "signal_type": "SHORT",
                "fear_score": fear_score,
                "entry_price": candle.close,
                "pair": "SOL/USDT",
                "timestamp": candle.timestamp,
                "confidence": min(fear_score / 100, 1.0),
                "trigger": (f"fear={fear_score:.1f}, "
                           f"vol_spike={vol_anomaly}, "
                           f"size={candle_range_pct:.1f}%, "
                           f"wick_ok={wick_rejection_ok}"),
            }
        elif (fear_score <= long_threshold and
              size_ok and wick_rejection_ok and self.position is None):
            signal = {
                "type": "MORNING_SPIKE",
                "signal_type": "LONG",
                "fear_score": fear_score,
                "entry_price": candle.close,
                "pair": "SOL/USDT",
                "timestamp": candle.timestamp,
                "confidence": min((100 - fear_score) / 100, 1.0),
                "trigger": (f"greed={100-fear_score:.1f}, "
                           f"vol_spike={vol_anomaly}, "
                           f"size={candle_range_pct:.1f}%, "
                           f"wick_ok={wick_rejection_ok}"),
            }

        if signal:
            self.signals.append(signal)
            return signal

        return None

    def execute_signal(self, signal: Dict, candle: Candle) -> Optional[Dict]:
        """Execute a signal: open position with risk management."""
        if self.position:
            return None

        price = candle.close
        side = signal["signal_type"]

        # Risk management
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
        close_reason = None
        exit_price = candle.close

        if pos["side"] == "SHORT":
            if candle.high >= pos["stop_loss"]:
                close_reason = "stop_loss"
                exit_price = pos["stop_loss"]
            elif candle.low <= pos["take_profit"]:
                close_reason = "take_profit"
                exit_price = pos["take_profit"]
        else:  # LONG
            if candle.low <= pos["stop_loss"]:
                close_reason = "stop_loss"
                exit_price = pos["stop_loss"]
            elif candle.high >= pos["take_profit"]:
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
    interval: str = "4h",
    start_price: float = 145.0,
    volatility: float = 0.03,
    seed: int = 42,
):
    """Generate synthetic OHLCV data with mean-reverting cycles.

    Creates a base price that oscillates around a center level using
    a sine wave, with occasional spikes that create mean-reversion
    opportunities for the strategy.

    Key properties:
    - Oscillates around start_price with ~15% amplitude
    - Spike events create sharp drops (fear) and pumps (greed)
    - After spikes, price reverts toward the mean
    - Volume spikes accompany large moves
    - Deterministic output given fixed seed

    This produces realistic mean-reversion patterns where:
    - Fear spikes → SHORT signals → reversion → take profit
    - Greed spikes → LONG signals → reversion → take profit
    """
    import random
    random.seed(seed)

    intervals = {"1h": 24, "4h": 6, "1d": 1}
    hours_per_day = intervals.get(interval, 24)
    total_candles = days * hours_per_day

    rows = [{"timestamp": "", "open": "", "high": "", "low": "", "close": "", "volume": ""}]

    price = start_price
    t = 0.0  # Time variable for sine wave
    dt = 1.0 / total_candles  # Time step (0-1 over the full dataset)

    # Generate mean-reverting price path
    for i in range(total_candles):
        # Base oscillation: sine wave with ~15% amplitude
        base = start_price
        amplitude = start_price * 0.12  # 12% amplitude
        base_price = base + amplitude * math.sin(2 * math.pi * t)

        # Mean reversion pull: price converges toward base oscillation
        deviation = (price - base_price) / base_price
        pull = -deviation * 0.2  # 20% of deviation pulled per candle
        noise = random.gauss(0, volatility * start_price * 0.3)
        price = price + pull + noise

        # Ensure price stays in a reasonable range (avoid runaway)
        low_bound = start_price * 0.7
        high_bound = start_price * 1.3
        if price < low_bound:
            price += (low_bound - price) * 0.5
        if price > high_bound:
            price -= (price - high_bound) * 0.5

       # Add occasional spike events (crashes and pumps) ~5% chance
        if random.random() < 0.05:
            spike_dir = 1 if random.random() > 0.5 else -1
            spike_mag = random.uniform(0.04, 0.08)  # 4-8% spike
            price *= (1 + spike_dir * spike_mag)

            # After spike: gradual reversion (4-6 candles back to mean)
            for j in range(5):
                nxt = i + j + 1
                if nxt < total_candles:
                    reversal = -spike_dir * random.uniform(0.02, 0.04)
                    price *= (1 + reversal)

        # Ensure price stays positive
        price = max(price, start_price * 0.5)

        open_price = price * (1 + random.gauss(0, volatility * 0.2))
        high = max(open_price, price) * (1 + abs(random.gauss(0, volatility * 0.5)))
        low = min(open_price, price) * (1 - abs(random.gauss(0, volatility * 0.5)))

        # Volume: spike during large price moves
        price_change = abs(price - open_price) / open_price if open_price > 0 else 0
        base_vol = 100000 + int(random.gauss(0, 30000))
        volume = base_vol * (1 + price_change * 20)  # Volume proportional to move
        volume = max(10000, volume)

        # Timestamp
        dt_obj = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        interval_secs = {"1h": 3600, "4h": 14400, "1d": 86400}.get(interval, 3600)
        ts = dt_obj.timestamp() + i * interval_secs

        rows.append({
            "timestamp": ts,
            "open": f"{open_price:.4f}",
            "high": f"{high:.4f}",
            "low": f"{low:.4f}",
            "close": f"{price:.4f}",
            "volume": str(volume),
        })

        t += dt

    # Write CSV
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows[1:])

    logger.info("Generated %d candles for %s -> %s", len(rows)-1, pair, output_path)


if __name__ == "__main__":
    sample_path = str(Path(__file__).parent / "data" / "sample_ohlcv.csv")
    generate_sample_ohlcv(sample_path)

    engine = BacktestEngine({
        "fear_threshold": 55,
        "greed_threshold": 50,
        "volatility_threshold": 2.0,
        "max_position_size": 0.1,
        "risk_per_trade": 0.02,
    })
    result = engine.run(sample_path)

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
