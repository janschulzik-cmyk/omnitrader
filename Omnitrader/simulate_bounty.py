#!/usr/bin/env python3
"""Simulate DAO bounty: add funds to dao_treasury and distribute.

Usage:
    python simulate_bounty.py [amount]

Adds the specified amount (default 50) to the DAO treasury pool via Hydra,
then distributes bounty rewards (70% reporter, 20% DAO, 10% burn).
"""

import os
import sys
import json
from datetime import datetime, timezone

os.environ.setdefault("PYTHONPATH", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.hydra import Hydra


def main():
    amount = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
    print(f"=== DAO Bounty Simulation ===")
    print(f"Amount: ${amount}")

    hydra = Hydra.load()

    # Check foundation balance
    foundation_balance = hydra.get_balance("foundation")
    print(f"Foundation balance: ${foundation_balance}")

    if foundation_balance < amount:
        print(f"ERROR: Foundation pool has insufficient funds ({foundation_balance} < {amount})")
        print("Adding funds to foundation pool first...")
        # Add funds from simulation
        initial = 100.0
        hydra.update_balance("foundation", initial - foundation_balance)
        foundation_balance = hydra.get_balance("foundation")
        print(f"Foundation balance now: ${foundation_balance}")

    # Step 1: Add funds to DAO treasury from foundation
    result = hydra.update_balance("foundation", -amount)
    hydra.update_balance("dao_treasury", amount)
    print(f"✓ Added ${amount} to DAO treasury from foundation pool")

    # Step 2: Simulate bounty distribution
    reporter_share = amount * 0.7
    dao_share = amount * 0.2
    burn_amount = amount * 0.1

    # Record distribution in DB
    from src.utils.db import get_session, SystemEvent
    session = get_session()
    try:
        event = SystemEvent(
            event_type="bounty_distribution",
            message=json.dumps({
                "bounty_amount": amount,
                "reporter_share": reporter_share,
                "dao_share": dao_share,
                "burn_amount": burn_amount,
                "tokenomics": {
                    "reporter_pct": 0.7,
                    "dao_pct": 0.2,
                    "burn_pct": 0.1,
                },
            }),
        )
        session.add(event)
        session.commit()
        print(f"✓ Distributed bounty: reporter=${reporter_share:.2f}, dao=${dao_share:.2f}, burn=${burn_amount:.2f}")
    except Exception as e:
        session.rollback()
        print(f"⚠ Failed to log distribution: {e}")
    finally:
        session.close()

    # Print final balances
    print(f"\n=== Final Hydra Balances ===")
    for pool in ["foundation", "dao_treasury", "foundation_research", "foundation_legal"]:
        bal = hydra.get_balance(pool)
        print(f"  {pool}: ${bal:.2f}")

    print(f"\n=== DAO Treasury Status ===")
    print(f"  Total: ${amount:.2f} (simulated)")


if __name__ == "__main__":
    main()
