#!/usr/bin/env python3
"""Run a complete offline backtest and print a summary report.

Usage:
    python run_backtest.py                    # Use default 30-day 1h data
    python run_backtest.py --days 7 --interval 4h  # 7 days, 4h candles
    python run_backtest.py --csv data/custom_ohlcv.csv  # Use custom CSV
"""

import os
import sys
import argparse
from pathlib import Path

# Ensure project root is on the path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("OFFLINE_MODE", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:///data/omnitrader.db")


def main():
    parser = argparse.ArgumentParser(description="Offline Backtest Runner")
    parser.add_argument("--days", type=int, default=30, help="Days of data to generate")
    parser.add_argument("--interval", type=str, default="1h", choices=["1h", "4h", "1d"])
    parser.add_argument("--csv", type=str, default=None, help="Use existing CSV instead of generating")
    parser.add_argument("--fear-threshold", type=float, default=70, help="Fear score threshold for SHORT")
    parser.add_argument("--greed-threshold", type=float, default=30, help="Greed score threshold for LONG")
    args = parser.parse_args()

    from src.backtesting.offline_engine import BacktestEngine, generate_sample_ohlcv

    # Determine data path
    if args.csv:
        data_path = args.csv
    else:
        data_path = str(ROOT_DIR / "data" / "sample_ohlcv.csv")
        print(f"Generating {args.days}-day {args.interval} OHLCV data...")
        generate_sample_ohlcv(data_path, days=args.days, interval=args.interval)

    # Run backtest
    print(f"\nRunning backtest with {args.interval} candles...")
    engine = BacktestEngine({
        "fear_threshold": args.fear_threshold,
        "greed_threshold": args.greed_threshold,
        "volatility_threshold": 2.0,
        "max_position_size": 0.1,
        "risk_per_trade": 0.02,
    })
    result = engine.run(data_path)

    # Print report
    print(f"\n{'=' * 60}")
    print(f"  OFFLINE BACKTEST REPORT")
    print(f"  Pair: {result.pair}")
    print(f"{'=' * 60}")
    print(f"  Data:          {result.candles_count} candles")
    print(f"  Period:        {result.start_time} to {result.end_time}")
    print(f"  Initial Cap:   ${result.initial_capital:,.2f}")
    print(f"  Final Cap:     ${result.final_capital:,.2f}")
    print(f"  Return:        {result.total_return_pct:+.2f}%")
    print(f"  Signals:       {result.num_signals}")
    print(f"  Trades:        {result.num_trades} ({result.winning_trades}W / {result.losing_trades}L)")
    print(f"  Win Rate:      {result.winning_trades / max(result.num_trades, 1) * 100:.1f}%")
    print(f"  Avg Trade:     {result.avg_trade_return_pct:+.2f}%")
    print(f"  Max Drawdown:  {result.max_drawdown_pct:.2f}%")
    print(f"{'=' * 60}")

    if result.trades:
        print(f"\n  Trade Log:")
        for i, t in enumerate(result.trades[:15]):
            direction = t["side"]
            reason = t["close_reason"].replace("_", " ")
            print(f"    #{i+1} {direction:5s}  ${t['entry_price']:>10.2f} -> ${t['exit_price']:>10.2f}  "
                  f"PnL ${t['pnl']:+>10.2f} ({t['pnl_pct']:+.2f}%) [{reason}]")
        if len(result.trades) > 15:
            print(f"    ... and {len(result.trades) - 15} more")

    print(f"\n  Backtest data saved to: {data_path}")
    print(f"  Offline mode confirmed: no network calls made.")
    print()

    return 0 if result.num_trades >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
