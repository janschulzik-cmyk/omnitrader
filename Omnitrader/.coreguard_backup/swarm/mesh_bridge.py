"""Mesh bridge: connects Omnitrader Striker to ai-mesh operator network.

Reads MESH_BRIDGE_ENABLED from env; if not "true", all operations are no-ops.
When enabled, it:
  - Posts every Striker trade as a signal to the ai-mesh operator API.
  - Polls /v1/operator/signals every 10 s and feeds SOL/USDT signals into
    the Striker's mean-reversion signal queue.
  - Exposes its status in GET /api/v1/status via the main app's status response.
"""

import os
import time
import asyncio
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional
from collections import deque

import httpx

from ..utils.logging_config import get_logger

logger = get_logger("swarm.mesh_bridge")

# ── Global state (singleton pattern) ─────────────────────────────────

_mesh_bridge_instance: Optional["MeshBridge"] = None
_trade_signal_queue: deque = deque(maxlen=200)
_incoming_signal_queue: deque = deque(maxlen=200)
_last_seen_signal_ids: set = set()


# ── MeshBridge ───────────────────────────────────────────────────────

class MeshBridge:
    """Bridge between Omnitrader Striker and the ai-mesh operator API."""

    def __init__(self, mesh_api_url: str = None):
        self.enabled = os.environ.get("MESH_BRIDGE_ENABLED", "false").lower() == "true"
        self.mesh_api_url = mesh_api_url or os.environ.get(
            "MESH_API_URL", "http://localhost:18081"
        )
        self._client: Optional[httpx.AsyncClient] = None
        self._connected = False
        self._last_signal: Optional[str] = None
        self._last_signal_ts: Optional[float] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the bridge: connect, begin polling loop."""
        if not self.enabled:
            logger.info("Mesh bridge disabled (MESH_BRIDGE_ENABLED != true).")
            return

        logger.info("Starting mesh bridge → %s", self.mesh_api_url)
        self._running = True
        self._client = httpx.AsyncClient(
            base_url=self.mesh_api_url, timeout=10.0, limits=httpx.Limits(max_connections=10)
        )
        self._connected = await self._try_connect()
        if self._connected:
            self._poll_task = asyncio.create_task(self._poll_signals_loop())
            logger.info("Mesh bridge connected and polling started.")
        else:
            logger.warning("Mesh bridge could not connect — polling not started.")

    async def stop(self) -> None:
        """Shut down the bridge and release resources."""
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Mesh bridge stopped.")

    # ── Post-trade hook ────────────────────────────────────────────

    def on_trade_executed(self, trade: Dict) -> None:
        """Called after a trade is placed by TradeExecutor.place_trade.

        Enqueues the trade as a signal for the background sender loop.

        Args:
            trade: Dict with keys like pair, side (SHORT/LONG),
                   entry_price, stop_loss, take_profit.
        """
        if not self.enabled:
            return

        try:
            side = trade.get("side", trade.get("signal_type", "SHORT"))
            signal = {
                "pair": trade.get("pair", "SOL/USDT"),
                "action": side.lower(),
                "entry": float(trade.get("entry_price", 0)),
                "stop": float(trade.get("stop_loss", 0)),
                "target": float(trade.get("take_profit", 0)),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            _trade_signal_queue.append(signal)
            logger.debug(
                "Trade signal queued: %s %s @ %.4f",
                signal["pair"], signal["action"], signal["entry"],
            )
        except Exception as e:
            logger.error("Failed to queue trade signal: %s", e)

    # ── Incoming signal injection ──────────────────────────────────

    def get_incoming_signal_queue(self) -> deque:
        """Return the deque of signals received from ai-mesh for the
        Striker's signal ingestion loop to consume.

        The consumer should call .pop() or iterate and clear.
        """
        return _incoming_signal_queue

    # ── Status ─────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        """Return the bridge status dict for inclusion in /api/v1/status."""
        return {
            "enabled": self.enabled,
            "connected": self._connected,
            "last_signal": self._last_signal,
        }

    # ── Internal helpers ───────────────────────────────────────────

    async def _try_connect(self) -> bool:
        """Try to reach the ai-mesh operator API."""
        try:
            resp = await self._client.get("/v1/operator/signals")
            resp.raise_for_status()
            self._connected = True
            logger.info("Mesh bridge connected to %s", self.mesh_api_url)
            return True
        except Exception as e:
            self._connected = False
            logger.warning("Mesh bridge connection failed: %s", e)
            return False

    async def _send_trade_signals(self) -> None:
        """Background loop: drain _trade_signal_queue and POST each."""
        while self._running:
            try:
                while _trade_signal_queue:
                    signal = _trade_signal_queue.popleft()
                    try:
                        resp = await self._client.post(
                            "/v1/operator/signal", json=signal
                        )
                        resp.raise_for_status()
                        self._last_signal = signal.get("pair", "unknown")
                        self._last_signal_ts = time.time()
                        logger.debug(
                            "Trade signal posted: %s %s",
                            signal["pair"], signal["action"],
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to post trade signal to %s: %s",
                            self.mesh_api_url, e,
                        )
                        # Re-enqueue so we don't lose it
                        _trade_signal_queue.append(signal)
                        break  # reconnect later
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Trade signal sender loop error: %s", e)
                await asyncio.sleep(2)

    async def _poll_signals_loop(self) -> None:
        """Background loop: poll /v1/operator/signals every 10 seconds
        and push new SOL/USDT signals into _incoming_signal_queue."""
        interval = int(os.environ.get("MESH_POLL_INTERVAL", "10"))
        while self._running:
            try:
                if not self._connected:
                    self._connected = await self._try_connect()
                    if not self._connected:
                        await asyncio.sleep(5)
                        continue

                resp = await self._client.get("/v1/operator/signals")
                resp.raise_for_status()
                signals = resp.json()
                if not isinstance(signals, list):
                    signals = [signals]

                for sig in signals:
                    sig_id = sig.get("id") or sig.get("signal_id") or (
                        sig.get("pair", "") + "_" + str(sig.get("timestamp", ""))
                    )
                    if sig_id in _last_seen_signal_ids:
                        continue

                    pair = sig.get("pair", "")
                    if "SOL/USDT" not in pair.upper():
                        continue

                    _incoming_signal_queue.append({
                        "source": "mesh_bridge",
                        "pair": pair,
                        "action": sig.get("action", sig.get("signal_type", "")),
                        "entry": float(sig.get("entry", sig.get("entry_price", 0))),
                        "stop": float(sig.get("stop", sig.get("stop_loss", 0))),
                        "target": float(sig.get("target", sig.get("take_profit", 0))),
                        "timestamp": sig.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    })
                    _last_seen_signal_ids.add(sig_id)
                    logger.info(
                        "Received mesh signal: %s %s @ %.4f",
                        pair, sig.get("action", ""), sig.get("entry", 0),
                    )

                # Evict old seen IDs (keep last 500)
                if len(_last_seen_signal_ids) > 500:
                    ids_list = list(_last_seen_signal_ids)
                    _last_seen_signal_ids = set(ids_list[-250:])

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Mesh bridge poll error: %s", e)
                self._connected = False
                await asyncio.sleep(5)


# ── Singleton accessor ───────────────────────────────────────────────

def get_mesh_bridge() -> MeshBridge:
    """Get (or create) the singleton MeshBridge instance."""
    global _mesh_bridge_instance
    if _mesh_bridge_instance is None:
        _mesh_bridge_instance = MeshBridge()
    return _mesh_bridge_instance


def reset_mesh_bridge() -> None:
    """Reset the singleton (useful for testing)."""
    global _mesh_bridge_instance, _trade_signal_queue, _incoming_signal_queue, _last_seen_signal_ids
    _mesh_bridge_instance = None
    _trade_signal_queue.clear()
    _incoming_signal_queue.clear()
    _last_seen_signal_ids.clear()


# ── Monkey-patch hook for TradeExecutor (if loaded at runtime) ─────

def patch_trade_executor_post_trade() -> None:
    """Monkey-patch TradeExecutor.place_trade to call the bridge on success.

    Call this early in the application lifecycle (e.g., during lifespan init).
    If TradeExecutor cannot be imported (CoreGuard encrypted + no loader),
    this is a silent no-op.
    """
    if not get_mesh_bridge().enabled:
        return

    try:
        from ..striker.trade_executor import TradeExecutor
        _original_place_trade = TradeExecutor.place_trade

        def _patched_place_trade(self, signal, pool_balance):
            result = _original_place_trade(self, signal, pool_balance)
            if result is not None:
                get_mesh_bridge().on_trade_executed(result)
            return result

        TradeExecutor.place_trade = _patched_place_trade
        logger.info("TradeExecutor.place_trade patched with mesh bridge hook.")
    except Exception as e:
        logger.warning(
            "Could not patch TradeExecutor.place_trade: %s. "
            "Mesh bridge trade signals will rely on external hooks.",
            e,
        )
