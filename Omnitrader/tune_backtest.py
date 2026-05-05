#!/usr/bin/env python3
"""Backtest parameter tuning grid search.

Runs multiple permutations over a parameter grid and outputs
a ranked table of performance metrics. Saves the best config
to config/best_params.yaml.

Usage:
    python tune_backtest.py                  # Run default grid
    python tune_backtest.py --days 90         # Use 90 days of data
    python tune_backtest.py --days 90 --csv data/custom.csv  # Custom data
    python tune_backtest.py --top 5           # Only show top 5 results
"""

import os
import sys
import time
import yaml
import argparse
import warnings
from pathlib import Path
from itertools import product as iter_product

warnings.filterwarnings("ignore")

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("OFFLINE_MODE", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:///data/omnitrader.db")

from src.backtesting.offline_engine import BacktestEngine, generate_sample_ohlcv


# Parameter Grid
PARAM_GRID = {
    "fear_threshold": [50, 55, 60],
    "volume_multiplier": [2.0, 2.5, 3.0],
    "min_candle_size_pct": [4.0, 5.0, 6.0, 7.0],
    "require_wick_rejection": [True, False],
}


def compute_metrics(result):
    """Compute risk-adjusted metrics for ranking."""
    trades = result.num_trades
    wins = result.winning_trades
    losses = result.losing_trades
    total_return = result.total_return_pct
    max_dd = result.max_drawdown_pct
    win_rate = wins / max(trades, 1) * 100
    avg_pnl = result.avg_trade_return_pct

    # Profit factor
    total_wins = sum(t["pnl_pct"] for t in result.trades if t["pnl"] > 0)
    total_losses = abs(sum(t["pnl_pct"] for t in result.trades if t["pnl"] <= 0))
    profit_factor = total_wins / max(total_losses, 0.01)

    # Sharpe ratio approximation
    if len(result.trades) >= 2:
        returns = [t["pnl_pct"] for t in result.trades]
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = variance ** 0.5 if variance > 0 else 0.01
        sharpe = (mean_r / std_r) * (252 ** 0.5) if std_r > 0 else 0
    else:
        sharpe = 0

    # Risk-adjusted score
    risk_adjusted = sharpe * profit_factor / (1 + max_dd / 100)

    return {
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "num_trades": trades,
        "winning_trades": wins,
        "losing_trades": losses,
        "avg_trade_return_pct": round(avg_pnl, 2),
        "risk_adjusted_score": round(risk_adjusted, 4),
    }


def run_grid_search(params_dir, days=30, custom_csv=None, top_n=20):
    """Run grid search over parameter combinations."""

    # Generate or load data
    if custom_csv:
        data_path = custom_csv
        print(f"Using custom CSV: {data_path}")
    else:
        data_path = str(params_dir / "data" / "tune_ohlcv.csv")
        print(f"Generating {days}-day 4h OHLCV data (higher volatility)...")
        generate_sample_ohlcv(
            data_path, days=days, interval="4h",
            volatility=0.06, seed=42,
        )

    # Build parameter combinations
    keys = sorted(PARAM_GRID.keys())
    values = [PARAM_GRID[k] for k in keys]
    combinations = list(iter_product(*values))

    print(f"\nRunning {len(combinations)} parameter combinations...")
    print(f"Parameters: {keys}\n")

    results = []
    start_time = time.time()

    for i, combo in enumerate(combinations):
        config = dict(zip(keys, combo))
        try:
            engine = BacktestEngine(config)
            result = engine.run(data_path)
            metrics = compute_metrics(result)
            metrics["params"] = config.copy()
            results.append(metrics)
        except Exception as e:
            results.append({
                "params": config.copy(),
                "total_return_pct": 0, "sharpe_ratio": 0,
                "max_drawdown_pct": 0, "win_rate_pct": 0,
                "profit_factor": 0, "num_trades": 0,
                "risk_adjusted_score": -9999,
            })

        if (i + 1) % 20 == 0:
            elapsed = time.time() - start_time
            print(f"  Completed {i+1}/{len(combinations)} ({elapsed:.1f}s)")

    elapsed = time.time() - start_time
    print(f"\nAll {len(combinations)} combinations done in {elapsed:.1f}s")

    # Sort by Sharpe ratio descending
    results.sort(key=lambda r: r["sharpe_ratio"], reverse=True)

    # Filter: profit_factor >= 1.0 and max_drawdown <= 10%
    valid = [
        r for r in results
        if r["profit_factor"] >= 1.0 and r["max_drawdown_pct"] <= 10.0
    ]

    print(f"\n{'=' * 90}")
    print(f"  TOP {min(top_n, len(valid))} PARAMETER SETS (by Sharpe ratio)")
    print(f"  (filtered: profit_factor >= 1.0, max_drawdown <= 10%)")
    print(f"{'=' * 90}")
    print(f"  {'#':>3}  {'Sharpe':>8}  {'Ret%':>8}  {'Win%':>6}  "
          f"{'PF':>5}  {'DD%':>6}  {'Trades':>6}")
    print(f"  {'─' * 56}")

    for idx, r in enumerate(valid[:top_n]):
        p = r["params"]
        print(f"  {idx+1:>3}  {r['sharpe_ratio']:>8.2f}  "
              f"{r['total_return_pct']:>7.2f}%  "
              f"{r['win_rate_pct']:>5.1f}%  {r['profit_factor']:>5.2f}  "
              f"{r['max_drawdown_pct']:>5.2f}%  {r['num_trades']:>6}")

    if valid:
        best = valid[0]
        print(f"\n{'=' * 90}")
        print(f"  BEST PARAMETER SET:")
        print(f"  {'=' * 90}")
        for k, v in best["params"].items():
            print(f"    {k}: {v}")
        print(f"\n  Metrics:")
        print(f"    Risk-Adj Score: {best['risk_adjusted_score']}")
        print(f"    Sharpe Ratio:   {best['sharpe_ratio']}")
        print(f"    Total Return:   {best['total_return_pct']}%")
        print(f"    Win Rate:       {best['win_rate_pct']}%")
        print(f"    Profit Factor:  {best['profit_factor']}")
        print(f"    Max Drawdown:   {best['max_drawdown_pct']}%")
        print(f"    Num Trades:     {best['num_trades']}")

        # Save best params
        best_config = {
            "backtest": {
                "fear_threshold": best["params"]["fear_threshold"],
                "volume_multiplier": best["params"]["volume_multiplier"],
                "min_candle_size_pct": best["params"]["min_candle_size_pct"],
                "require_wick_rejection": best["params"]["require_wick_rejection"],
            }
        }
        best_path = params_dir / "config" / "best_params.yaml"
        best_path.parent.mkdir(parents=True, exist_ok=True)
        best_path.write_text(yaml.dump(best_config, default_flow_style=False))
        print(f"\n  Best params saved to: {best_path}")

        # Save top 3 candidates
        candidates = []
        for r in valid[:3]:
            candidates.append({
                "rank": len(candidates) + 1,
                "sharpe_ratio": r["sharpe_ratio"],
                "profit_factor": r["profit_factor"],
                "max_drawdown_pct": r["max_drawdown_pct"],
                "win_rate_pct": r["win_rate_pct"],
                "total_return_pct": r["total_return_pct"],
                "num_trades": r["num_trades"],
                "params": {
                    "fear_threshold": r["params"]["fear_threshold"],
                    "volume_multiplier": r["params"]["volume_multiplier"],
                    "min_candle_size_pct": r["params"]["min_candle_size_pct"],
                    "require_wick_rejection": r["params"]["require_wick_rejection"],
                }
            })

        candidates_path = params_dir / "config" / "param_candidates.yaml"
        candidates_path.write_text(
            yaml.dump({"candidates": candidates}, default_flow_style=False)
        )
        print(f"  Top 3 candidates saved to: {candidates_path}")

        return best["params"]
    else:
        print("\n  No valid parameter sets found.")
        return None


def main():
    parser = argparse.ArgumentParser(description="Backtest Parameter Tuning")
    parser.add_argument("--days", type=int, default=30, help="Days of data to generate")
    parser.add_argument("--csv", type=str, default=None, help="Use existing CSV")
    parser.add_argument("--top", type=int, default=10, help="Number of top results to show")
    args = parser.parse_args()

    best_params = run_grid_search(
        ROOT_DIR, days=args.days,
        custom_csv=args.csv, top_n=args.top,
    )

    if best_params:
        print(f"\n\u2713 Best params selected and saved.")
        print(f"  Apply via environment variables:")
        for k, v in best_params.items():
            env_key = k.upper()
            print(f"    {env_key}={v}")
        return 0
    else:
        print("\n\u2717 No valid parameter sets found.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
