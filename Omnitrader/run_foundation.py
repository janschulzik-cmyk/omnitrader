#!/usr/bin/env python3
"""Run the Foundation politician tracker manually."""

import os
import sys
import json
import yaml

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

# Load .env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Load settings
with open(os.path.join(os.path.dirname(__file__), "config", "settings.yaml")) as f:
    settings = yaml.safe_load(f)

from src.foundation.politician_tracker import PoliticianTracker

def main():
    # Temporarily disable real-money trading for safety
    original_val = os.environ.get("FOUNDATION_REAL_MONEY", "false")
    os.environ["FOUNDATION_REAL_MONEY"] = "false"

    tracker = PoliticianTracker(config=settings)
    print(f"High-profile members: {tracker.high_profile}")
    print(f"Min transaction value: {tracker.min_transaction_value}")

    # Load congress sample data
    fixture_path = os.path.join(
        os.path.dirname(__file__), "tests", "fixtures", "congress_sample.json"
    )
    if not os.path.exists(fixture_path):
        print(f"Fixture not found: {fixture_path}")
        sys.exit(1)

    with open(fixture_path) as f:
        trades = json.load(f)

    print(f"Loaded {len(trades)} congress trades from fixture")

    # Filter high-profile trades
    filtered = tracker.filter_high_profile_trades(trades)
    print(f"Filtered to {len(filtered)} high-profile trades")

    # Get tradable signals
    pool_balance = float(os.environ.get("FOUNDATION_POOL_BALANCE", "100.00"))
    signals = tracker.get_tradable_signals(filtered, pool_balance)
    print(f"Generated {len(signals)} tradable signals")

    for s in signals:
        print(f"  Signal: {s.get('token')} (ticker={s.get('ticker')}) "
              f"from {s.get('politician')} ${s.get('amount_usd')}")

    # Execute signals
    results = tracker.execute_signals(signals, pool_balance)
    print(f"\nExecution results: {len(results)}")
    for r in results:
        print(f"  {r.get('token')}: {r.get('status')} order_id={r.get('order_id')} tag={r.get('tag')}")

    # Restore original
    os.environ["FOUNDATION_REAL_MONEY"] = original_val
    print("\nDone.")

if __name__ == "__main__":
    main()
