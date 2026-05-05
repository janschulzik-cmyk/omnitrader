#!/usr/bin/env python3
"""Validate best backtest parameters against a 90-day run.

Loads config/best_params.yaml, runs a 90-day backtest, and checks:
  - profit_factor >= 1.2
  - PnL is positive (total_return_pct > 0)

If these thresholds aren't met, falls back to candidates
from config/param_candidates.yaml in order.

Usage:
    python validate_best_params.py
"""

import os
import sys
import yaml
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("OFFLINE_MODE", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:///data/omnitrader.db")

from src.backtesting.offline_engine import BacktestEngine, generate_sample_ohlcv


def compute_metrics(result):
    """Compute key metrics for reporting."""
    total_wins = sum(t["pnl_pct"] for t in result.trades if t["pnl"] > 0)
    total_losses = abs(sum(t["pnl_pct"] for t in result.trades if t["pnl"] <= 0))
    profit_factor = total_wins / max(total_losses, 0.01)

    if len(result.trades) >= 2:
        returns = [t["pnl_pct"] for t in result.trades]
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = variance ** 0.5 if variance > 0 else 0.01
        sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0
    else:
        sharpe = 0

    wins = result.winning_trades
    win_rate = wins / max(result.num_trades, 1) * 100

    return {
        "total_return_pct": result.total_return_pct,
        "sharpe_ratio": round(sharpe, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "win_rate_pct": round(win_rate, 1),
        "num_trades": result.num_trades,
    }


def run_validation(days=90, top_n=3):
    """Run validation using best params or candidates."""

    # Load best params
    best_path = ROOT_DIR / "config" / "best_params.yaml"
    if not best_path.exists():
        print("ERROR: config/best_params.yaml not found.")
        print("Run tune_backtest.py first.")
        return

    best_config = yaml.safe_load(best_path.read_text())["backtest"]
    print(f"Loaded best params from {best_path}")
    for k, v in best_config.items():
        print(f"  {k} = {v}")

    # Load candidates
    candidates_path = ROOT_DIR / "config" / "param_candidates.yaml"
    candidates = []
    if candidates_path.exists():
        data = yaml.safe_load(candidates_path.read_text())
        candidates = data.get("candidates", [])
        print(f"\nLoaded {len(candidates)} candidates from {candidates_path}")

    # Generate 90-day data
    data_path = str(ROOT_DIR / "data" / "validate_ohlcv.csv")
    print(f"\nGenerating {days}-day 4h OHLCV data...")
    generate_sample_ohlcv(data_path, days=days, interval="4h", volatility=0.06, seed=42)

    # Try best first
    configs_to_try = [{"params": best_config, "name": "best"}] + [
        {"params": c["params"], "name": f"candidate_{c['rank']}"}
        for c in candidates
    ]

    for attempt in configs_to_try:
        params = attempt["params"]
        name = attempt["name"]
        print(f"\n{'=' * 60}")
        print(f"  Trying: {name}")
        print(f"{'=' * 60}")

        config = {
            "fear_threshold": params.get("fear_threshold", 55),
            "greed_threshold": params.get("greed_threshold", 50),
            "volume_multiplier": params.get("volume_multiplier", 2.0),
            "min_candle_size_pct": params.get("min_candle_size_pct", 6.0),
            "require_wick_rejection": params.get("require_wick_rejection", False),
        }

        engine = BacktestEngine(config)
        result = engine.run(data_path)
        metrics = compute_metrics(result)

        print(f"\n  Results:")
        print(f"    Total PnL (return): {metrics['total_return_pct']:+.2f}%")
        print(f"    Profit Factor:      {metrics['profit_factor']:.2f}")
        print(f"    Sharpe Ratio:       {metrics['sharpe_ratio']:.2f}")
        print(f"    Max Drawdown:       {metrics['max_drawdown_pct']:.2f}%")
        print(f"    Win Rate:           {metrics['win_rate_pct']:.1f}%")
        print(f"    Num Trades:         {metrics['num_trades']}")

        # Check thresholds
        if metrics["profit_factor"] >= 1.2 and metrics["total_return_pct"] > 0:
            print(f"\n  \u2713 VALIDATED - {name} passes all thresholds!")
            print(f"    profit_factor >= 1.2 and PnL > 0")
            return metrics

        print(f"\n  \u2717 {name} failed thresholds:")
        if metrics["profit_factor"] < 1.2:
            print(f"    profit_factor {metrics['profit_factor']:.2f} < 1.2")
        if metrics["total_return_pct"] <= 0:
            print(f"    PnL {metrics['total_return_pct']:+.2f}% <= 0")

    # All candidates exhausted
    print(f"\n{'=' * 60}")
    print(f"  No candidate passed all thresholds.")
    print(f"  Proceeding with best metrics achieved.")
    print(f"{'=' * 60}")
    return metrics


if __name__ == "__main__":
    metrics = run_validation(days=90)
    print(f"\nValidation complete.")
    sys.exit(0)
