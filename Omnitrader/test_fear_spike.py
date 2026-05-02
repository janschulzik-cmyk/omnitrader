#!/usr/bin/env python3
"""End-to-end fear spike simulation: headline → news monitor → signal → trade."""
import sys
import os
import json
from datetime import datetime
from dotenv import load_dotenv

SRC_DIR = "/home/joe/ouroboros/cathedral/Omnitrader"
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

env_path = os.path.join(SRC_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path, override=True)

for mod in list(sys.modules.keys()):
    if mod.startswith('src'):
        del sys.modules[mod]

from src.hydra import Hydra
from src.striker.news_monitor import NewsMonitor
from src.striker.mean_reversion import MeanReversionSignalGenerator
from src.striker.trade_executor import TradeExecutor
from src.utils.db import get_session, Trade
from src.utils.logging_config import get_logger
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = get_logger("fear_spike_test")

API_KEY = os.environ.get("EXCHANGE_API_KEY", "")
API_SECRET = os.environ.get("EXCHANGE_API_SECRET", "")

print("=" * 70)
print("OMNITRADER FEAR SPIKE END-TO-END TEST")
print("=" * 70)

# ── Step 1: Verify no open trades ───────────────────────────────────────
print("\n[1/8] Checking for open trades...")
session = get_session()
try:
    open_trades = session.query(Trade).filter(Trade.is_closed == False).all()
    if open_trades:
        print(f"    WARNING: {len(open_trades)} open trade(s) found")
        for t in open_trades:
            print(f"      id={t.id}, pair={t.pair}, side={t.side}, status={t.status}")
    else:
        print("    No open trades — clean state confirmed.")
finally:
    session.close()

# ── Step 2: Load Hydra ──────────────────────────────────────────────────
print("\n[2/8] Loading Hydra capital pools...")
hydra = Hydra.load()
status = hydra.get_status()
striker_balance = status.get("striker", 0)
print(f"    Hydra status: {status}")
print(f"    Striker pool balance: ${striker_balance:.2f}")

# ── Step 3: Pre-populate NewsMonitor fear history ───────────────────────
print("\n[3/8] Creating NewsMonitor — pre-populating fear history with LOW baselines...")
nm = NewsMonitor({})

# Pre-populate with LOW baseline values so a sudden high fear will spike
# The detect_spike logic: current_fear - past_fear >= 30 (spike_increase)
# So if past_fear ~ 20, we need current_fear > 50
baseline_scores = [15.0, 18.0, 12.0, 16.0]
for score in baseline_scores:
    nm.fear_history.append(score)
print(f"    Baseline fear_history: {list(nm.fear_history)}")

# ── Step 4: Inject mock fear headline ───────────────────────────────────
# We directly set a high fear score since TextBlob can't produce >1.0 polarity.
# In production, many articles with strong negative sentiment would produce this.
MOCK_HEADLINE = (
    "BREAKING: Iran threatens to close Strait of Hormuz — "
    "oil prices surge 8% in pre-market"
)

print(f"\n[4/8] Injecting fear-triggering headline:")
print(f"    '{MOCK_HEADLINE}'")

# The user-specified headline — store it for the trade record
nm.latest_headlines = [{
    "title": MOCK_HEADLINE,
    "description": "Geopolitical escalation in the Middle East as Iran threatens closure "
                   "of the Strait of Hormuz, a critical chokepoint for global oil supply.",
    "source": "Reuters",
    "published_at": datetime.utcnow().isoformat(),
}]

# Simulate what the monitor would compute: inject a high fear score
# This represents many negatively-sentimented articles from the news feed
fear_score = 85.0  # High fear — simulating a market panic

nm.fear_history.append(fear_score)
spike_event = nm.detect_spike(fear_score)

past_fear = list(nm.fear_history)[-4]
spike_increase = fear_score - past_fear

print(f"    Fear score injected (simulating negative news feed): {fear_score:.1f}")
print(f"    Fear history now: {list(nm.fear_history)}")
print(f"    Past fear (4 intervals ago): {past_fear:.1f}")
print(f"    Spike increase: {spike_increase:.1f} points")
print(f"    Spike detection result: {spike_event}")

if spike_event == "FEAR_SPIKE":
    print("    ✓ FEAR_SPIKE detected! (fear jumped {:.1f} points)".format(spike_increase))
else:
    print(f"    ✗ No FEAR_SPIKE — check thresholds")

# ── Step 5: Generate trading signal ─────────────────────────────────────
print("\n[5/8] Generating trading signal...")
signal_config = {
    "trading_pair": "SOL/USDT",
    "ohlcv_timeframe": "15m",
    "spike_increase": 30,
    "max_concurrent_trades": 3,
    "min_wick_ratio": 0.3,
    "min_volume_multiplier": 2.0,
    "min_candle_move_pct": 5.0,
    "news": {},
    "signal_cooldown_minutes": 0,
    "exchange": {
        "name": "binance",
        "testnet": True,
        "API_KEY": os.environ.get("EXCHANGE_API_KEY", ""),
        "API_SECRET": os.environ.get("EXCHANGE_API_SECRET", ""),
    },
}

if not API_KEY or API_KEY == "your_api_key_here":
    print("    ✗ EXCHANGE_API_KEY not configured")
    signal = None
else:
    generator = MeanReversionSignalGenerator(signal_config, hydra)
    signal = generator.generate_signal(fear_score, spike_event)

    if signal:
        print(f"    ✓ Signal generated:")
        print(f"      Type: {signal.get('signal_type')}")
        print(f"      Pair: {signal.get('pair')}")
        print(f"      Entry: ${signal.get('entry_price', 'N/A')}")
        print(f"      Stop Loss: ${signal.get('stop_loss', 'N/A')}")
        print(f"      Take Profit: ${signal.get('take_profit', 'N/A')}")
        print(f"      Confidence: {signal.get('confidence')}")
        print(f"      Trigger: {signal.get('trigger')}")
        print(f"      Candle Pattern: {signal.get('candle_pattern')}")
        print(f"      Volume Anomaly: {signal.get('volume_anomaly')}")
    else:
        print("    ✗ No signal generated")
        if spike_event != "FEAR_SPIKE":
            print(f"      - No FEAR_SPIKE event")
        if fear_score < 70:
            print(f"      - Fear score {fear_score:.1f} < 70 threshold")
        # Fallback check
        if fear_score >= 70 and spike_event == "FEAR_SPIKE":
            print(f"      - FEAR_SPIKE + fear>=70 but _generate_short_signal returned None")
            print(f"        (likely OHLCV data insufficient or no confirming pattern)")

# ── Step 6: Execute trade ───────────────────────────────────────────────
print("\n[6/8] Executing trade on Binance testnet...")
if not signal or signal.get("signal_type") != "SHORT":
    print("    Skipping trade execution (no SHORT signal).")
    result = None
else:
    try:
        executor = TradeExecutor(signal_config.get("exchange", {}))
        result = executor.place_trade(signal, striker_balance)
        if result:
            print(f"    ✓ Trade placed successfully:")
            print(f"      Order ID: {result.get('order_id')}")
            print(f"      Trade DB ID: {result.get('trade_id')}")
            print(f"      Pair: {result.get('pair')}")
            print(f"      Side: {result.get('side')}")
            print(f"      Entry: ${result.get('entry_price')}")
            print(f"      Size: {result.get('size')}")
            print(f"      Stop Loss: ${result.get('stop_loss')}")
            print(f"      Take Profit: ${result.get('take_profit')}")
        else:
            print("    ✗ TradeExecutor returned None")
    except Exception as e:
        print(f"    ✗ Error placing trade: {e}")
        import traceback
        traceback.print_exc()
        result = None

# ── Step 7: Check DB ────────────────────────────────────────────────────
print("\n[7/8] Checking database for new trade record...")
session = get_session()
try:
    all_trades = session.query(Trade).order_by(Trade.id.desc()).all()
    print(f"    Total trades in DB: {len(all_trades)}")
    if all_trades:
        latest = all_trades[0]
        print(f"    Latest trade:")
        print(f"      id={latest.id}, pair={latest.pair}, side={latest.side}")
        print(f"      entry={latest.entry_price}, qty={latest.quantity}")
        print(f"      status={latest.status}, order_id={latest.exchange_order_id}")
        print(f"      sl={latest.stop_loss}, tp={latest.take_profit}")
        print(f"      trigger_fear={latest.trigger_fear_score}")
        print(f"      candle_pattern={latest.candle_pattern}")
        if latest.is_closed:
            print(f"      outcome={latest.outcome}")
            print(f"      pnl={latest.pnl}, pnl_pct={latest.pnl_pct}")
        else:
            print(f"      Trade is OPEN — awaiting stop-loss/take-profit")
    else:
        print("    No trade records in database")
finally:
    session.close()

# ── Step 8: Verify via API ──────────────────────────────────────────────
print("\n[8/8] Verifying via REST API...")
import urllib.request
api_url = "http://localhost:8000/api/v1/trades"
req = urllib.request.Request(api_url, headers={"x-api-key": "omnitrader-api-key-change-me"})
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        trades_json = json.loads(resp.read().decode())
        print(f"    API returned {len(trades_json)} trades:")
        for t in trades_json:
            print(f"      id={t['id']}, pair={t['pair']}, side={t['side']}, "
                  f"entry={t['entry_price']}, status={t['status']}")
except Exception as e:
    print(f"    API error: {e}")

# ── Summary ─────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"  Headline: {MOCK_HEADLINE}")
print(f"  Fear Score: {fear_score}")
print(f"  Spike Event: {spike_event}")
if signal:
    print(f"  Signal: {signal.get('signal_type')} on {signal.get('pair')} @ ${signal.get('entry_price')}")
if result:
    print(f"  Trade Placed: id={result.get('trade_id')}, side={result.get('side')}, status=placed")
else:
    print(f"  Trade: NOT placed (signal_type={signal.get('signal_type') if signal else 'N/A'})")
print("=" * 70)
