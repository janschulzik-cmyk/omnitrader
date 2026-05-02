"""Dry run test: Place a mock panic fade trade on Binance testnet."""
import sys
import os
import json
from datetime import datetime
from dotenv import load_dotenv

SRC_DIR = "/home/joe/ouroboros/cathedral/Omnitrader"
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Load .env into os.environ first (needed for security module)
env_path = os.path.join(SRC_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path, override=True)
    print(f"    Loaded .env from {env_path}")

# Clear stale cached modules
for mod in list(sys.modules.keys()):
    if mod.startswith('src'):
        del sys.modules[mod]

from src.hydra import Hydra
from src.striker.trade_executor import TradeExecutor
from src.utils.db import get_session, Trade
from src.utils.logging_config import get_logger

logger = get_logger("dry_run")

print("=" * 60)
print("OMNITRADER DRY RUN - Panic Fade Trade Test")
print("=" * 60)

# --- Step 1: Load Hydra ---
print("\n[1/5] Loading Hydra capital pools...")
hydra = Hydra.load()
status = hydra.get_status()
print(f"    Initial Hydra status: {status}")

# --- Step 2: Mock panic fade signal ---
signal = {
    "pair": "SOL/USDT",
    "signal_type": "LONG",
    "entry_price": 125.0,
    "stop_loss": 120.0,
    "take_profit": 135.0,
    "confidence": 0.85,
    "reason": "mock FEAR_SPIKE",
    "fear_score": 92,
    "trigger": "FEAR_SPIKE",
    "headline": "Mock: Iran Hormuz fear spike",
    "volume_anomaly": 3.0,
    "candle_pattern": "shooting_star",
}
print(f"\n[2/5] Mock panic fade signal:")
print(f"    Pair: {signal['pair']}")
print(f"    Signal: {signal['signal_type']}")
print(f"    Entry: ${signal['entry_price']}")
print(f"    Stop Loss: ${signal['stop_loss']}")
print(f"    Take Profit: ${signal['take_profit']}")
print(f"    Fear Score: {signal['fear_score']}")

# --- Step 3: Load exchange config and execute trade ---
print(f"\n[3/5] Executing trade via TradeExecutor...")

# Load .env for API keys (already loaded above, but verify)
config = {
    "name": "binance",
    "testnet": True,
    "risk_management": {
        "risk_per_trade": 0.02,  # 2% of pool
    },
}

if not os.environ.get("EXCHANGE_API_KEY") or os.environ["EXCHANGE_API_KEY"] == "your_api_key_here":
    print("    ERROR: EXCHANGE_API_KEY not configured in .env")
    sys.exit(1)

print(f"    API key: {os.environ['EXCHANGE_API_KEY'][:8]}...{os.environ['EXCHANGE_API_KEY'][-4:]}")
print(f"    Testnet: {config['testnet']}")

try:
    executor = TradeExecutor(exchange_config=config)
    # status is flat dict: {'striker': 70.0, 'foundation': 20.0, 'moat': 10.0}
    pool_balance = status["striker"]
    result = executor.place_trade(signal, pool_balance)
    print(f"\n    Result: {json.dumps(result, indent=2, default=str)}")
except Exception as e:
    print(f"\n    ERROR placing trade: {e}")
    import traceback
    traceback.print_exc()
    result = None

# --- Step 4: Check results ---
print(f"\n[4/5] Trade result summary:")
if result:
    print(f"    Order ID: {result.get('order_id', 'N/A')}")
    print(f"    Pair: {result.get('pair', 'N/A')}")
    print(f"    Signal: {result.get('side', 'N/A')}")
    print(f"    Size: {result.get('size', 'N/A')}")
    print(f"    Entry: ${result.get('entry_price', 'N/A')}")
    print(f"    Stop Loss: ${result.get('stop_loss', 'N/A')}")
    print(f"    Take Profit: ${result.get('take_profit', 'N/A')}")
    print(f"    Risk Amount: ${result.get('risk_amount', 'N/A'):.2f}")
    print(f"    Trade DB ID: {result.get('trade_id', 'N/A')}")
else:
    print("    No result - trade may not have been placed")

# --- Step 5: Check DB ---
print(f"\n[5/5] Latest trade in database:")
try:
    session = get_session()
    trade = session.query(Trade).order_by(Trade.id.desc()).first()
    if trade:
        print(f"    id={trade.id}, pair={trade.pair}, side={trade.side}")
        print(f"    entry={trade.entry_price}, qty={trade.quantity}, risk={trade.risk_amount}")
        print(f"    status={trade.status}, order_id={trade.exchange_order_id}")
        print(f"    sl={trade.stop_loss}, tp={trade.take_profit}")
        print(f"    fear={trade.trigger_fear_score}, pattern={trade.candle_pattern}")
    else:
        print("    No trade records found in database")
    session.close()
except Exception as e:
    print(f"    DB error: {e}")

print("\n" + "=" * 60)
print("DRY RUN COMPLETE")
print("=" * 60)
