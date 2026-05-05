"""Trade Executor for Striker Module.

Uses ccxt to interact with cryptocurrency exchanges.
Places orders, monitors positions, and handles stop-loss/take-profit.
"""

import os
import time
from datetime import datetime
from typing import Dict, List, Optional

import ccxt
import yaml

from ..utils.db import Trade, get_session, log_system_event
from ..utils.logging_config import get_logger
from ..risk.position_sizer import fractional_kelly, calc_position_size
from ..risk.correlation import check_correlation

logger = get_logger("striker.executor")


class TradeExecutor:
    """Executes trades on cryptocurrency exchanges via ccxt."""

    _instance = None

    def __init__(self, exchange_config: Dict = None, market_type: str = "spot"):
        """Initialize the trade executor.

        Args:
            exchange_config: Exchange configuration dict from settings.yaml.
            market_type: Either "spot" (default) or "futures" for perpetual futures.
        """
        self.exchange_config = exchange_config or {}
        self.exchange = None
        self.market_type = market_type
        self.offline_mode = os.environ.get("OFFLINE_MODE", "false").lower() == "true"
        self.backtest_mode = os.environ.get("BACKTEST_MODE", "false").lower() == "true"
        self._initialize_exchange()

    @classmethod
    def load(cls, exchange_config: Dict = None, market_type: str = "spot") -> "TradeExecutor":
        """Load or return the singleton TradeExecutor instance.

        Args:
            exchange_config: Optional exchange config override.
            market_type: Either "spot" or "futures".

        Returns:
            TradeExecutor instance.
        """
        if cls._instance is None:
            cls._instance = cls(exchange_config, market_type)
        return cls._instance

    def _initialize_exchange(self) -> None:
        """Initialize the ccxt exchange instance."""
        exchange_name = self.exchange_config.get("name", "binance").lower()
        testnet = self.exchange_config.get("testnet", True)

        # Load .env if not already loaded (so keys are in os.environ)
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env"))

        # Read API keys directly from environment (testnet mode)
        api_key = os.environ.get("EXCHANGE_API_KEY", "")
        api_secret = os.environ.get("EXCHANGE_API_SECRET", "")

        if not api_key or not api_secret:
            logger.warning(
                "Exchange API keys not configured. Trade execution disabled."
            )
            return

        # Create exchange instance
        exchange_class = getattr(ccxt, exchange_name, None)
        if exchange_class is None:
            logger.error("Unknown exchange: %s", exchange_name)
            return

        config = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }

        # Use spot market for testnet (swap doesn't work on Binance testnet)
        if testnet:
            config["options"] = {
                "defaultType": "spot",
            }

        self.exchange = exchange_class(config)

        if testnet:
            self.exchange.set_sandbox_mode(True)
            # Suppress rate-limit warning for symbol-less open orders fetch
            self.exchange.options["warnOnFetchOpenOrdersWithoutSymbol"] = False

        logger.info(
            "Exchange initialized: %s (testnet=%s)",
            exchange_name, testnet,
        )

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 100,
    ) -> List[List[float]]:
        """Fetch OHLCV data from the exchange.

        Args:
            symbol: Trading pair (e.g., 'SOL/USDT').
            timeframe: Candle timeframe.
            limit: Number of candles to fetch.

        Returns:
            List of [timestamp, open, high, low, close, volume] arrays.
        """
        if not self.exchange:
            logger.error("Exchange not initialized.")
            return []

        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            # ccxt returns [timestamp, open, high, low, close, volume]
            return ohlcv
        except Exception as e:
            logger.error("Failed to fetch OHLCV for %s: %s", symbol, e)
            return []

    def get_market_price(self, symbol: str) -> float:
        """Get the current market price for a trading pair.

        Args:
            symbol: Trading pair (e.g., 'SOL/USDT').

        Returns:
            Current market price, or 0 on failure.
        """
        if not self.exchange:
            return 0

        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker.get("last", 0) or ticker.get("close", 0)
        except Exception as e:
            logger.error("Failed to get price for %s: %s", symbol, e)
            return 0

    def calculate_position_size(
        self,
        risk_amount: float,
        entry_price: float,
        stop_loss: float,
        side: str,
    ) -> float:
        """Calculate position size based on risk amount and stop loss.

        Args:
            risk_amount: Amount to risk (e.g., 2% of pool).
            entry_price: Entry price.
            stop_loss: Stop loss price.
            side: 'SHORT' or 'LONG'.

        Returns:
            Position size in base currency.
        """
        price_diff = abs(entry_price - stop_loss)
        if price_diff <= 0:
            logger.warning("Invalid stop loss: entry=%.4f, stop=%.4f", entry_price, stop_loss)
            return 0

        position_size = risk_amount / price_diff
        return round(position_size, 8)

    def place_trade(
        self,
        signal: Dict,
        pool_balance: float,
    ) -> Optional[Dict]:
        """Place a trade based on a signal.

        Args:
            signal: Signal dict from MeanReversionSignalGenerator.
            pool_balance: Current Striker pool balance.

        Returns:
            Trade dict with order details, or None on failure.
        """
        if not self.exchange:
            logger.error("Cannot place trade: exchange not initialized.")
            return None

        # ── Phase 2.2: Circuit breaker check ────────────────────────
        try:
            from ..hydra import Hydra
            hydra = Hydra.load()
            cb_result = hydra.check_circuit_breaker()
            if cb_result.get("breaker_triggered"):
                logger.error(
                    "Circuit breaker tripped: %s — aborting trade.",
                    cb_result.get("message", ""),
                )
                return None
        except Exception as e:
            logger.warning("Circuit breaker check failed (proceeding): %s", e)

        signal_type = signal.get("signal_type", "NEUTRAL")
        if signal_type == "NEUTRAL":
            logger.info("No trade to place: signal is NEUTRAL.")
            return None

        pair = signal.get("pair", "SOL/USDT")
        entry_price = signal.get("entry_price", 0)
        stop_loss = signal.get("stop_loss", 0)
        take_profit = signal.get("take_profit", 0)
        confidence = signal.get("confidence", 0)

        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            logger.error("Invalid signal parameters: %s", signal)
            return None

        # Calculate risk amount (2% of pool)
        risk_pct = self.exchange_config.get("risk_management", {}).get("risk_per_trade", 0.02)
        risk_amount = pool_balance * risk_pct

        # Calculate position size (stop-based)
        position_size = self.calculate_position_size(
            risk_amount, entry_price, stop_loss, signal_type
        )

        if position_size <= 0:
            logger.error("Calculated position size is zero or negative.")
            return None

        # ── Phase 2.1: Kelly sizing ────────────────────────────────
        try:
            session = get_session()
            try:
                closed = (
                    session.query(Trade)
                    .filter(Trade.pair == pair, Trade.is_closed == True)
                    .order_by(Trade.created_at.desc())
                    .limit(20)
                    .all()
                )
            finally:
                session.close()

            if len(closed) >= 5:
                wins = sum(1 for t in closed if t.outcome == "WIN")
                win_rate = wins / len(closed)
                win_pcts = [t.pnl_pct for t in closed if t.outcome == "WIN" and t.pnl_pct > 0]
                loss_pcts = [abs(t.pnl_pct) for t in closed if t.outcome == "LOSS" and t.pnl_pct < 0]
                avg_win_pct = sum(win_pcts) / len(win_pcts) if win_pcts else 1.0
                avg_loss_pct = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0.5
            else:
                # Load defaults from best_params.yaml
                try:
                    cfg_path = os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "config", "best_params.yaml"
                    )
                    with open(cfg_path) as f:
                        cfg = yaml.safe_load(f)
                    win_rate = cfg.get("default_win_rate", 0.45)
                    avg_win_loss_ratio = cfg.get("default_avg_win_loss_ratio", 1.5)
                    avg_win_pct = avg_win_loss_ratio * 1.0  # relative to loss
                    avg_loss_pct = 1.0
                except Exception:
                    win_rate = 0.45
                    avg_win_pct = 1.5
                    avg_loss_pct = 1.0

            kelly_fraction = fractional_kelly(
                win_rate, avg_win_pct, avg_loss_pct
            )
            if kelly_fraction > 0:
                kelly_size = self.calculate_position_size(
                    pool_balance * kelly_fraction,
                    entry_price,
                    stop_loss,
                    signal_type,
                )
                position_size = min(position_size, kelly_size)
                logger.info(
                    "Kelly sizing: stop-based=%.6f, kelly=%.6f -> using %.6f (win_rate=%.2f, wins=%d/%d)",
                    self.calculate_position_size(risk_amount, entry_price, stop_loss, signal_type),
                    kelly_size, position_size,
                    win_rate, sum(1 for t in closed if t.outcome == "WIN") if len(closed) >= 5 else 0,
                    len(closed) if len(closed) >= 5 else 0,
                )
            else:
                logger.info("Kelly edge negative; using stop-based size %.6f", position_size)
        except Exception as e:
            logger.warning("Kelly sizing fallback (using stop-based): %s", e)

        # ── Phase 2.3: Correlation check ───────────────────────────
        try:
            # Gather open positions from DB
            session = get_session()
            try:
                open_trades_db = (
                    session.query(Trade)
                    .filter(Trade.is_closed == False)
                    .all()
                )
            finally:
                session.close()

            open_positions = []
            for ot in open_trades_db:
                open_positions.append({
                    "pair": ot.pair,
                    "side": ot.side,
                })

            corr_result = check_correlation(
                open_positions=open_positions,
                new_signal={"pair": pair, "signal_type": signal_type},
                ccxt_exchange=self.exchange,
            )

            if corr_result.get("is_highly_correlated"):
                old_size = position_size
                position_size *= corr_result.get("size_multiplier", 1.0)
                logger.info(
                    "Correlation adjustment: %.3f, size %.6f -> %.6f (%s)",
                    corr_result.get("correlation", 0),
                    old_size, position_size,
                    corr_result.get("adjustment_reason", ""),
                )
        except Exception as e:
            logger.warning("Correlation check fallback: %s", e)

        if position_size <= 0:
            logger.error("Adjusted position size is zero or negative after Kelly/correlation.")
            return None

        try:
            # Determine order type and side
            order_side = "sell" if signal_type == "SHORT" else "buy"
            # Use limit order when entry_price is provided (works on testnet spot)
            if entry_price > 0:
                order_type = "limit"
            else:
                order_type = "market"

            # Place the main order
            order = self.exchange.create_order(
                symbol=pair,
                type=order_type,
                side=order_side,
                amount=position_size,
                price=entry_price if order_type == "limit" else None,
            )

            logger.info(
                "Trade placed: %s %s %.8f @ %.4f (%s pool=%.2f)",
                signal_type, pair, position_size, entry_price,
                order.get("id", "unknown"), pool_balance,
            )

            # Save to database
            trade_id = self._save_trade_to_db(
                signal=signal,
                order=order,
                position_size=position_size,
                risk_amount=risk_amount,
                stop_loss=stop_loss,
                take_profit=take_profit,
                pool_balance=pool_balance,
            )

            # Analyst Bureau metadata logging
            if signal.get("analyst_bureau_used"):
                report_summary = signal.get("debate_summary", "No summary")
                logger.info("Trade executed with Analyst Bureau insight: %s", report_summary)
                logger.info("Analyst consensus: %s, confidence_modifier: %.2f, risk_adjustment: %.2f",
                            signal.get("analyst_consensus", "unknown"),
                            signal.get("confidence_modifier", 1.0),
                            signal.get("risk_adjustment", 1.0))

            return {
                "order_id": order.get("id"),
                "pair": pair,
                "side": signal_type,
                "size": position_size,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "risk_amount": risk_amount,
                "trade_id": trade_id,
            }

        except ccxt.InsufficientFunds as e:
            logger.error("Insufficient funds for trade: %s", e)
            return None
        except ccxt.InvalidOrder as e:
            logger.error("Invalid order: %s", e)
            return None
        except Exception as e:
            logger.error("Failed to place trade: %s", e)
            return None

    def _save_trade_to_db(
        self,
        signal: Dict,
        order: Dict,
        position_size: float,
        risk_amount: float,
        stop_loss: float,
        take_profit: float,
        pool_balance: float,
    ) -> Trade:
        """Save trade details to the database.

        Args:
            signal: The trading signal dict.
            order: The exchange order dict.
            position_size: Calculated position size.
            risk_amount: Risk amount for this trade.
            stop_loss: Stop loss price.
            take_profit: Take profit price.
            pool_balance: Striker pool balance.

        Returns:
            Saved Trade record.
        """
        session = get_session()
        try:
            trade = Trade(
                pair=signal.get("pair", "SOL/USDT"),
                side=signal.get("signal_type", "SHORT"),
                entry_price=signal.get("entry_price", 0),
                quantity=position_size,
                risk_amount=risk_amount,
                stop_loss=stop_loss,
                take_profit=take_profit,
                pnl=0.0,
                pnl_pct=0.0,
                outcome=None,
                is_closed=False,
                status="PENDING",
                trigger_fear_score=signal.get("fear_score"),
                trigger_headline=signal.get("trigger", ""),
                volume_anomaly=bool(signal.get("volume_anomaly", False)),
                candle_pattern=signal.get("candle_pattern"),
                exchange_order_id=order.get("id", ""),
            )
            session.add(trade)
            session.commit()
            # Capture ID before session close
            trade_id = trade.id
            return trade_id
        except Exception as e:
            session.rollback()
            logger.error("Failed to save trade to DB: %s", e)
            raise
        finally:
            session.close()

    def check_open_positions(self) -> List[Dict]:
        """Check all open positions on the exchange.

        For spot trading, we check open orders and recent fills.
        For futures, we use fetch_positions().

        Returns:
            List of open position dicts with PnL info.
        """
        if not self.exchange:
            return []

        try:
            # For spot markets, check open orders (limit orders still pending)
            open_orders = self.exchange.fetch_open_orders()

            active_positions = []
            for order in open_orders:
                if float(order.get("remaining", 0)) > 0:
                    active_positions.append({
                        "symbol": order.get("symbol"),
                        "side": "buy" if order.get("side") == "buy" else "sell",
                        "size": float(order.get("remaining", 0)),
                        "entry_price": float(order.get("price", 0)),
                        "mark_price": order.get("average"),
                        "unrealized_pnl": None,
                        "stop_loss": None,
                        "take_profit": None,
                        "order_type": order.get("type"),
                        "timestamp": order.get("timestamp"),
                    })

            logger.info("Found %d open spot orders", len(active_positions))
            return active_positions

        except Exception as e:
            logger.error("Failed to check positions: %s", e)
            return []

    def close_position(self, symbol: str, side: str) -> Optional[Dict]:
        """Close an open position.

        Args:
            symbol: Trading pair symbol.
            side: 'buy' or 'sell' (opposite of position side).

        Returns:
            Close order dict, or None on failure.
        """
        if not self.exchange:
            return None

        try:
            order = self.exchange.create_market_order(symbol, side, {"reduceOnly": True})
            logger.info("Position closed: %s %s", symbol, side)
            return order
        except Exception as e:
            logger.error("Failed to close position %s: %s", symbol, e)
            return None

    def cancel_all_orders(self) -> int:
        """Cancel all open orders on the exchange.

        Returns:
            Number of orders cancelled.
        """
        if not self.exchange:
            return 0

        try:
            orders = self.exchange.fetch_open_orders()
            cancelled = 0

            for order in orders:
                try:
                    self.exchange.cancel_order(order["id"], order["symbol"])
                    cancelled += 1
                except Exception as e:
                    logger.warning("Failed to cancel order %s: %s", order.get("id"), e)

            logger.info("Cancelled %d open orders", cancelled)
            return cancelled

        except Exception as e:
            logger.error("Failed to cancel orders: %s", e)
            return 0

    def get_exchange_status(self) -> Dict:
        """Get exchange connection status.

        Returns:
            Dict with connection info.
        """
        if not self.exchange:
            return {"connected": False, "error": "Exchange not initialized"}

        try:
            status = self.exchange.fetch_status()
            return {
                "connected": status.get("status") == "ok",
                "status": status.get("status"),
                "updated": status.get("updated"),
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}

    def _fetch_filled_trades(self, symbol: str, side: str, limit: int = 20) -> List[Dict]:
        """Fetch filled trades from the exchange for a given symbol and side.

        Uses ccxt's fetch_my_trades to get recent fills.

        Args:
            symbol: Trading pair symbol (e.g., 'SOL/USDT').
            side: 'buy' or 'sell'.
            limit: Max number of trades to fetch.

        Returns:
            List of filled trade dicts with price, amount, and timestamp.
        """
        if not self.exchange:
            return []

        try:
            trades = self.exchange.fetch_my_trades(symbol, limit=limit)
            filled = []
            for t in trades:
                if t.get("side") == side and float(t.get("filled", 0)) > 0:
                    filled.append({
                        "order_id": t.get("id", ""),
                        "price": float(t.get("price", 0)),
                        "amount": float(t.get("amount", 0)),
                        "cost": float(t.get("cost", 0)),
                        "fee": t.get("fee", {}),
                        "timestamp": t.get("timestamp"),
                        "datetime": t.get("datetime"),
                    })
            logger.debug("Found %d filled %s trades for %s", len(filled), side, symbol)
            return filled
        except Exception as e:
            logger.error("Failed to fetch filled trades for %s: %s", symbol, e)
            return []

    def _calculate_pnl(self, trade: Dict, exit_price: float) -> Dict:
        """Calculate PnL for a closed trade.

        Args:
            trade: Trade dict with entry_price, quantity, side.
            exit_price: Actual fill price from the exchange.

        Returns:
            Dict with pnl, pnl_pct, and outcome (WIN/LOSS/BREAKEVEN).
        """
        entry_price = trade.get("entry_price", 0)
        quantity = trade.get("quantity", 0)
        side = trade.get("side", "LONG")
        risk_amount = trade.get("risk_amount", 1)

        if entry_price <= 0 or quantity <= 0:
            return {"pnl": 0.0, "pnl_pct": 0.0, "outcome": "UNKNOWN"}

        # PnL for spot:
        # LONG: (exit - entry) * quantity
        # SHORT: (entry - exit) * quantity
        if side == "LONG":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        # PnL percentage relative to the notional entry value
        entry_value = entry_price * quantity
        pnl_pct = (pnl / entry_value * 100) if entry_value > 0 else 0.0

        # Determine outcome
        if pnl > 0.01:
            outcome = "WIN"
        elif pnl < -0.01:
            outcome = "LOSS"
        else:
            outcome = "BREAKEVEN"

        return {
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "outcome": outcome,
        }

    def _update_trade_pnl(self, trade_id: int, exit_price: float, filled_amount: float) -> bool:
        """Update a trade record in the DB with PnL data.

        Args:
            trade_id: Database trade ID.
            exit_price: Fill price from the exchange.
            filled_amount: Quantity that was filled.

        Returns:
            True if the trade was found and updated successfully.
        """
        from ..utils.db import Trade, get_session

        session = get_session()
        try:
            trade = session.query(Trade).filter_by(id=trade_id).first()
            if not trade:
                logger.warning("Trade %d not found for PnL update", trade_id)
                return False

            # Build a minimal trade dict for PnL calculation
            trade_dict = {
                "entry_price": trade.entry_price,
                "quantity": trade.quantity,
                "side": trade.side,
                "risk_amount": trade.risk_amount,
            }

            pnl_info = self._calculate_pnl(trade_dict, exit_price)

            # Use filled_amount if it differs from the original quantity
            # (partial fills)
            actual_quantity = filled_amount if filled_amount > 0 else trade.quantity
            trade.exit_price = exit_price
            trade.pnl = pnl_info["pnl"]
            trade.pnl_pct = pnl_info["pnl_pct"]
            trade.outcome = pnl_info["outcome"]
            trade.is_closed = True
            trade.status = pnl_info["outcome"]

            session.commit()
            logger.info(
                "Trade #%d closed: exit=%.4f pnl=$%.2f (%.2f%%) outcome=%s",
                trade_id, exit_price, pnl_info["pnl"],
                pnl_info["pnl_pct"], pnl_info["outcome"],
            )
            return True

        except Exception as e:
            session.rollback()
            logger.error("Failed to update PnL for trade %d: %s", trade_id, e)
            return False
        finally:
            session.close()

    def _match_trade_to_exit_fill(self, trade_id: int, symbol: str, open_side: str) -> Optional[Dict]:
        """Find the matching exit fill for a trade.

        When a LONG trade closes, we look for a recent SELL fill on the
        same symbol. When a SHORT trade closes, we look for a BUY fill.

        Args:
            trade_id: Database trade ID (for logging).
            symbol: Trading pair symbol.
            open_side: The side of the open trade (LONG or SHORT).

        Returns:
            Dict with exit_price and filled_amount, or None.
        """
        # The exit side is the opposite of the open side
        # trade.side is stored as LONG/SHORT, so map to ccxt buy/sell
        exit_side = "sell" if open_side == "LONG" else "buy"

        fills = self._fetch_filled_trades(symbol, exit_side)
        if not fills:
            logger.warning("No %s fills found for trade #%d on %s", exit_side, trade_id, symbol)
            return None

        # Use the most recent fill as the exit
        latest_fill = fills[0]
        return {
            "exit_price": latest_fill["price"],
            "filled_amount": latest_fill["amount"],
        }

    def monitor_closed_trades(self) -> Dict:
        """Scan open trades, detect closures on the exchange, calculate PnL, and update DB.

        This is the main entry point called periodically (e.g., every 30 seconds)
        by the order monitoring loop.

        Returns:
            Dict with summary: {closed: N, pnl_total: $X.XX, trades: [...]}.
        """

        from ..utils.db import Trade, get_session

        if not self.exchange:
            return {"closed": 0, "error": "Exchange not initialized"}

        session = get_session()
        try:
            # Fetch all open trades from the DB
            open_trades = session.query(Trade).filter_by(is_closed=False).all()
            if not open_trades:
                return {"closed": 0}

            closed_count = 0
            total_pnl = 0.0
            results = []

            for trade in open_trades:
                symbol = trade.pair

                try:
                    # Map trade side (LONG/SHORT) to ccxt side (buy/sell) for API calls
                    # LONG -> buy (entry), SHORT -> sell (entry)
                    ccxt_entry_side = "buy" if trade.side == "LONG" else "sell"

                    # Check if the original entry order is filled
                    fills = self._fetch_filled_trades(symbol, ccxt_entry_side)
                    if not fills:
                        # No fill yet — position still open
                        continue

                    # We have a filled entry. Now look for the matching exit.
                    # First check if there's already an exit_price recorded
                    # (from a previous monitoring cycle).
                    if trade.exit_price is not None:
                        continue

                    # Find the matching exit fill
                    exit_data = self._match_trade_to_exit_fill(
                        trade.id, symbol, trade.side
                    )
                    if not exit_data:
                        # Exit hasn't happened yet — entry filled but position still open
                        # Mark as FILLED so check_open_positions knows we have a real position
                        trade.status = "FILLED"
                        session.commit()
                        continue

                    # Exit fill found — close the trade
                    ok = self._update_trade_pnl(
                        trade.id,
                        exit_data["exit_price"],
                        exit_data["filled_amount"],
                    )
                    if ok:
                        closed_count += 1
                        total_pnl += trade.pnl
                        results.append({
                            "trade_id": trade.id,
                            "pair": trade.pair,
                            "side": trade.side,
                            "entry_price": trade.entry_price,
                            "exit_price": exit_data["exit_price"],
                            "pnl": trade.pnl,
                            "pnl_pct": trade.pnl_pct,
                            "outcome": trade.outcome,
                        })

                except Exception as e:
                    logger.error("Error monitoring trade #%d: %s", trade.id, e)
                    continue

            return {
                "closed": closed_count,
                "total_pnl": round(total_pnl, 2),
                "trades": results,
            }

        except Exception as e:
            logger.error("Error in monitor_closed_trades: %s", e)
            return {"closed": 0, "error": str(e)}
        finally:
            session.close()

    # ====================================================================
    # Futures Trading Methods
    # ====================================================================

    def _ensure_futures_exchange(self) -> None:
        """Ensure the exchange is configured for futures trading."""
        if self.market_type != "futures" or self.exchange is None:
            return

        # Configure for futures (e.g., Binance USDT perpetuals)
        self.exchange.set_sandbox_mode(True)  # testnet by default
        self.exchange.options.setdefault("defaultType", "swap")  # perpetual futures

    def place_futures_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        leverage: int = 1,
        stop_loss: float = None,
        take_profit: float = None,
    ) -> Optional[Dict]:
        """Place a futures order.

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT').
            side: 'buy' or 'sell'.
            amount: Size of position.
            order_type: 'market', 'limit', etc.
            leverage: Leverage multiplier (default 1x).
            stop_loss: Stop loss price.
            take_profit: Take profit price.

        Returns:
            Order dict or None.
        """
        if self.exchange is None:
            logger.error("Exchange not initialized for futures trading")
            return None

        try:
            self._ensure_futures_exchange()

            # Set leverage
            if leverage > 1:
                try:
                    self.exchange.set_leverage(leverage, symbol)
                    logger.info("Set leverage %dx for %s", leverage, symbol)
                except Exception:
                    logger.warning("Failed to set leverage, continuing")

            # Place order
            kwargs = {"type": order_type}
            if order_type == "limit":
                # Price must be provided for limit orders
                logger.warning("Limit orders require price parameter")
                return None

            order = self.exchange.create_order(symbol, order_type, side, amount, **kwargs)

            # Set stop-loss if provided
            if stop_loss:
                try:
                    self.exchange.create_stop_loss_order(symbol, side, stop_loss, amount)
                    logger.info("Stop-loss set at %s for %s", stop_loss, symbol)
                except Exception:
                    logger.warning("Failed to set stop-loss")

            # Set take-profit if provided
            if take_profit:
                try:
                    self.exchange.create_take_profit_order(symbol, side, take_profit, amount)
                    logger.info("Take-profit set at %s for %s", take_profit, symbol)
                except Exception:
                    logger.warning("Failed to set take-profit")

            # Save to DB
            self._save_futures_trade_to_db(symbol, side, amount, order)

            return order
        except Exception as e:
            logger.error("Futures order failed: %s", e)
            return None

    def fetch_futures_positions(self, symbol: str = None) -> List[Dict]:
        """Fetch open futures positions.

        Args:
            symbol: Optional symbol filter.

        Returns:
            List of position dicts.
        """
        if self.exchange is None:
            return []

        try:
            self._ensure_futures_exchange()
            positions = self.exchange.fetch_positions([symbol] if symbol else None)

            result = []
            for pos in positions:
                if float(pos.get("contracts", 0)) > 0:
                    result.append({
                        "symbol": pos.get("symbol"),
                        "side": pos.get("side"),
                        "size": float(pos.get("contracts", 0)),
                        "entry_price": float(pos.get("entryPrice", 0)),
                        "mark_price": float(pos.get("markPrice", 0)),
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                        "leverage": pos.get("leverage"),
                        "liquidation_price": pos.get("liquidationPrice"),
                    })

            return result
        except Exception as e:
            logger.error("Failed to fetch futures positions: %s", e)
            return []

    def close_futures_position(self, symbol: str, side: str = None) -> Optional[Dict]:
        """Close a futures position.

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT').
            side: 'long' or 'short' (auto-detected if None).

        Returns:
            Close order dict or None.
        """
        if self.exchange is None:
            return None

        try:
            self._ensure_futures_exchange()

            # Fetch current position
            positions = self.fetch_futures_positions(symbol)
            if not positions:
                logger.warning("No open position for %s", symbol)
                return None

            pos = positions[0]
            close_side = "sell" if pos["side"] == "long" else "buy"
            amount = pos["size"]

            # Close position
            order = self.exchange.create_order(
                symbol, "market", close_side, amount
            )

            return order
        except Exception as e:
            logger.error("Failed to close futures position: %s", e)
            return None

    def _save_futures_trade_to_db(
        self, symbol: str, side: str, amount: float, order: Dict
    ) -> None:
        """Save futures trade to database."""
        try:
            session = get_session()
            trade = Trade(
                symbol=symbol,
                side=side,
                amount=amount,
                order_type="futures_" + order.get("type", "market"),
                status="filled",
                exchange="futures_" + self.exchange_config.get("name", "binance"),
                raw_response=str(order),
            )
            session.add(trade)
            session.commit()
            session.close()
        except Exception:
            session.close()
            logger.warning("Failed to save futures trade to DB")

    def calculate_futures_position_size(
        self,
        pool_size: float,
        risk_per_trade: float = 0.005,  # 0.5% max risk
        leverage: int = 1,
    ) -> float:
        """Calculate position size for futures with risk management.

        Ensures total loss never exceeds risk_per_trade of pool.

        Args:
            pool_size: Total pool size (Striker pool).
            risk_per_trade: Max risk as fraction (default 0.5%).
            leverage: Leverage multiplier.

        Returns:
            Position size in base currency.
        """
        max_loss = pool_size * risk_per_trade
        # Position size = max_loss * leverage / entry_price (approximate)
        # For now, return the raw max loss amount
        return max_loss * leverage
