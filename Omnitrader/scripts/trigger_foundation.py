"""Trigger the Foundation politician tracker with cached congress data."""
import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.foundation.politician_tracker import PoliticianTracker
from src.utils.db import get_session, Trade
import yaml

def main():
    # Load congress sample data
    fixture_path = "tests/fixtures/congress_sample.json"
    with open(fixture_path, "r") as f:
        congress_trades = json.load(f)

    print(f"Loaded {len(congress_trades)} congress trades from fixture")

    # Load config
    config_path = "config/settings.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Initialize tracker
    tracker = PoliticianTracker(
        config=config,
        token_map_path="config/token_map.yaml",
    )

    # Filter high-profile trades
    filtered = tracker.filter_high_profile_trades(congress_trades)
    print(f"Filtered to {len(filtered)} high-profile trades")

    # Get foundation pool balance
    foundation_pool = float(os.environ.get("FOUNDATION_POOL_BALANCE", "100.00"))
    print(f"Foundation pool balance: ${foundation_pool:.2f}")

    # Generate tradable signals
    signals = tracker.get_tradable_signals(filtered, foundation_pool)
    print(f"Generated {len(signals)} tradable signals")

    for sig in signals:
        print(f"  Signal: {sig.get('ticker')} -> {sig.get('token')} "
              f"${sig.get('amount_usd'):.2f} by {sig.get('politician')}")

    # Execute signals
    results = tracker.execute_signals(signals, foundation_pool)
    print(f"\nExecution results: {len(results)} trades")

    for r in results:
        print(f"  Status: {r.get('status')} | Token: {r.get('token')} "
              f"|$ {r.get('amount_usd'):.2f} | Order ID: {r.get('order_id')}")

    # Log system event
    from src.utils.db import log_system_event
    for r in results:
        log_system_event(
            event_type="FOUNDA TION_TRADE",
            severity="INFO",
            message=f"Foundation trade executed: {r.get('token')} "
                    f"${r.get('amount_usd'):.2f} ({r.get('status')})",
            details=json.dumps(r),
        )

    print(f"\n✓ Foundation execution complete. Check /api/v1/trades for results.")

if __name__ == "__main__":
    main()