"""Politician Tracker for Foundation Module.

Fetches and tracks US Congress financial disclosures, mapping
traded stocks to crypto tokens for automated buying.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx
import yaml

from ..utils.db import PoliticianTrade, get_session, log_system_event
from ..utils.logging_config import get_logger

logger = get_logger("foundation.politician")


class PoliticianTracker:
    """Tracks congressional trades and generates buy signals."""

    def __init__(
        self,
        config: Dict = None,
        token_map_path: str = None,
    ):
        """Initialize the politician tracker.

        Args:
            config: Configuration dict from settings.yaml.
            token_map_path: Path to token_map.yaml.
        """
        self.config = config or {}
        self.high_profile = self.config.get("congress", {}).get(
            "high_profile_members", []
        )
        self.min_transaction_value = self.config.get("congress", {}).get(
            "min_transaction_value", 1000
        )
        self.api_url = self.config.get("congress", {}).get(
            "api_url", "https://api.apify.com/v2/acts/actor/scrape-congress-trades"
        )
        self.api_key = os.environ.get("QUIVER_API_KEY", "")

        # Load token map
        self.token_map_path = token_map_path or "config/token_map.yaml"
        self.token_map = self._load_token_map()

        # Confidence tracking
        self.politician_confidence: Dict[str, float] = {}

    def _load_token_map(self) -> Dict:
        """Load the token mapping file.

        Returns:
            Dict mapping stock tickers to token info.
        """
        try:
            if os.path.exists(self.token_map_path):
                with open(self.token_map_path, "r") as f:
                    data = yaml.safe_load(f)
                    return data.get("mapping", {})
        except Exception as e:
            logger.error("Failed to load token map: %s", e)
        return {}

    def fetch_congress_trades(self) -> List[Dict]:
        """Fetch the latest congressional trades.

        Uses Apify actor or Quiver Quant API.

        Returns:
            List of trade dicts.
        """
        if not self.api_key:
            logger.warning("Quiver/Apify API key not configured. Using simulated data.")
            return self._generate_simulated_trades()

        try:
            # Apify actor endpoint
            url = f"{self.api_url}?token={self.api_key}&limit=100"
            response = httpx.get(url, timeout=30.0)
            response.raise_for_status()
            data = response.json()

            trades = data.get("data", [])
            logger.info("Fetched %d congressional trades", len(trades))
            return trades

        except httpx.HTTPError as e:
            logger.error("Failed to fetch congressional trades: %s", e)
            return self._generate_simulated_trades()
        except Exception as e:
            logger.error("Unexpected error fetching trades: %s", e)
            return self._generate_simulated_trades()

    def filter_high_profile_trades(self, trades: List[Dict]) -> List[Dict]:
        """Filter trades from high-profile members.

        Args:
            trades: List of trade dicts.

        Returns:
            Filtered list containing only high-profile member trades.
        """
        filtered = []
        for trade in trades:
            member_name = trade.get("member_name") or trade.get("name", "")
            if member_name in self.high_profile:
                trade_value = trade.get("value_low", 0)
                if trade_value >= self.min_transaction_value:
                    filtered.append(trade)

        logger.info(
            "Filtered %d high-profile trades (from %d total)",
            len(filtered), len(trades),
        )
        return filtered

    def map_to_token(self, ticker: str) -> Optional[Dict]:
        """Map a stock ticker to a crypto token.

        Args:
            ticker: Stock ticker symbol.

        Returns:
            Token info dict, or None if no mapping exists.
        """
        return self.token_map.get(ticker.upper())

    def get_tradable_signals(
        self, trades: List[Dict], foundation_pool: float
    ) -> List[Dict]:
        """Convert filtered trades into tradable signals.

        Args:
            trades: Filtered congressional trades.
            foundation_pool: Current Foundation pool balance.

        Returns:
            List of signal dicts with token, amount, and politician info.
        """
        signals = []

        for trade in trades:
            ticker = trade.get("ticker", "").upper()
            politician = trade.get("member_name") or trade.get("name", "Unknown")
            transaction_type = trade.get("transaction_type", "").upper()

            # Only consider BUY transactions
            if transaction_type != "BUY":
                continue

            # Check if we have a token mapping
            token_info = self.map_to_token(ticker)
            if token_info is None:
                logger.info("No token mapping for ticker %s. Skipping.", ticker)
                continue

            # Calculate trade amount (10% of foundation pool per trade, max)
            trade_amount = foundation_pool * 0.10
            max_trade = 1000.00  # Max $1000 per political trade

            # Check politician confidence
            confidence = self.politician_confidence.get(politician, 50.0)
            if confidence < 50.0:
                logger.info(
                    "Skipping trade from %s: confidence %.2f < 50",
                    politician, confidence,
                )
                continue

            signal = {
                "politician": politician,
                "ticker": ticker,
                "token": token_info.get("token", ticker),
                "protocol": token_info.get("protocol", "unknown"),
                "network": token_info.get("network", "ethereum"),
                "amount_usd": min(trade_amount, max_trade),
                "confidence": confidence,
                "filing_date": trade.get("filing_date", ""),
                "transaction_value": trade.get("value_low", 0),
            }
            signals.append(signal)

        logger.info("Generated %d tradable signals from congressional trades", len(signals))
        return signals

    def record_trade_signal(self, signal: Dict) -> PoliticianTrade:
        """Record a politician trade signal in the database.

        Args:
            signal: Signal dict.

        Returns:
            Saved PoliticianTrade record.
        """
        session = get_session()
        try:
            trade = PoliticianTrade(
                politician_name=signal.get("politician", ""),
                stock_ticker=signal.get("ticker", ""),
                transaction_type="BUY",
                transaction_value=signal.get("transaction_value", 0),
                filing_date=datetime.utcnow(),
                mapped_token=signal.get("token", ""),
                execution_status="PENDING",
                politician_confidence=signal.get("confidence", 50.0),
            )
            session.add(trade)
            session.commit()
            return trade
        except Exception as e:
            session.rollback()
            logger.error("Failed to record politician trade: %s", e)
            raise
        finally:
            session.close()

    def update_confidence(
        self, politician: str, forward_return_pct: float
    ) -> None:
        """Update a politician's confidence score based on forward returns.

        Args:
            politician: Politician name.
            forward_return_pct: Actual return percentage over the tracking window.
        """
        current = self.politician_confidence.get(politician, 50.0)

        # Simple exponential moving average
        alpha = 0.1  # Weight for new data
        self.politician_confidence[politician] = (
            alpha * min(100.0, max(0.0, forward_return_pct * 10))  # Scale return to 0-100
            + (1 - alpha) * current
        )

        logger.info(
            "Updated %s confidence: %.2f -> %.2f",
            politician, current, self.politician_confidence[politician],
        )

    def get_confidence_scores(self) -> Dict[str, float]:
        """Get current confidence scores for all politicians.

        Returns:
            Dict mapping politician names to confidence scores.
        """
        return dict(self.politician_confidence)

    def _generate_simulated_trades(self) -> List[Dict]:
        """Generate simulated congressional trades for testing.

        Returns:
            List of simulated trade dicts.
        """
        import random

        tickers = ["AAPL", "NVDA", "MSFT", "GOOGL", "RTX", "LMT", "ENB", "XOM"]
        members = ["Nancy Pelosi", "Josh Crenshaw", "Tom Emmer", "Marsha Blackburn"]

        trades = []
        for _ in range(random.randint(3, 8)):
            trades.append({
                "member_name": random.choice(members),
                "ticker": random.choice(tickers),
                "transaction_type": "BUY",
                "value_low": random.uniform(1000, 50000),
                "value_high": random.uniform(50000, 500000),
                "filing_date": datetime.utcnow().isoformat(),
            })

        logger.info("Generated %d simulated congressional trades", len(trades))
        return trades

    def execute_signals(
        self,
        signals: List[Dict],
        pool_balance: float = None,
    ) -> List[Dict]:
        """Execute foundation buy orders for politician signals.

        Uses the TradeExecutor singleton to place spot buy orders.

        Args:
            signals: List of signal dicts from get_tradable_signals().
            pool_balance: Foundation pool balance (defaults to config env var).

        Returns:
            List of execution result dicts.
        """
        from ..striker.trade_executor import TradeExecutor

        max_per_trade = float(
            os.environ.get("FOUNDATION_MAX_PER_TRADE", "5.00")
        )
        use_real_money = os.environ.get("FOUNDATION_REAL_MONEY", "false").lower() == "true"

        if pool_balance is None:
            pool_balance = float(
                os.environ.get("FOUNDATION_POOL_BALANCE", "100.00")
            )

        results = []
        executor = TradeExecutor.load()

        for signal in signals:
            token = signal.get("token", "")
            amount_usd = min(signal.get("amount_usd", 0), max_per_trade)

            if amount_usd <= 0:
                logger.info("Skipping: amount_usd=%.2f (max=%.2f)", amount_usd, max_per_trade)
                continue

            logger.info(
                "Foundation buy signal: %s (ticker=%s) politician=%s $%.2f",
                token, signal.get("ticker"), signal.get("politician"), amount_usd,
            )

            if not use_real_money:
                # Dry-run / paper trading mode
                trade_record = {
                    "politician": signal.get("politician"),
                    "token": token,
                    "ticker": signal.get("ticker"),
                    "amount_usd": amount_usd,
                    "side": "BUY",
                    "status": "PAPER",
                    "tag": "foundation",
                    "order_id": f"paper-{signal.get('ticker', 'UNKNOWN')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                }
                results.append(trade_record)

                # Record in DB as foundation trade
                try:
                    session = get_session()
                    try:
                        trade = PoliticianTrade(
                            politician_name=signal.get("politician", ""),
                            stock_ticker=signal.get("ticker", ""),
                            transaction_type="BUY",
                            transaction_value=amount_usd,
                            filing_date=datetime.utcnow(),
                            mapped_token=token,
                            execution_status="EXECUTED",
                            politician_confidence=signal.get("confidence", 50.0),
                        )
                        session.add(trade)
                        session.commit()
                        logger.info("Recorded foundation trade in DB: %s", token)
                    finally:
                        session.close()
                except Exception as e:
                    logger.warning("Failed to record foundation trade in DB: %s", e)

            else:
                # Real money mode — place actual order
                symbol = f"{token}/USDT"
                # Get current price
                try:
                    price = executor.get_market_price(symbol)
                    if price <= 0:
                        logger.error("Cannot get price for %s. Skipping.", symbol)
                        continue
                    quantity = amount_usd / price
                except Exception as e:
                    logger.error("Failed to fetch price for %s: %s", symbol, e)
                    continue

                try:
                    order = executor.exchange.create_order(
                        symbol=symbol,
                        type="limit",
                        side="buy",
                        amount=quantity,
                        price=price,
                    )
                    order_id = order.get("id", "unknown")
                    logger.info(
                        "Foundation REAL MONEY order placed: %s %s %.8f @ %.6f order_id=%s",
                        signal.get("ticker"), token, quantity, price, order_id,
                    )

                    trade_record = {
                        "politician": signal.get("politician"),
                        "token": token,
                        "ticker": signal.get("ticker"),
                        "amount_usd": amount_usd,
                        "side": "BUY",
                        "status": "LIVE",
                        "tag": "foundation",
                        "order_id": order_id,
                        "price": price,
                        "quantity": quantity,
                    }
                    results.append(trade_record)
                except Exception as e:
                    logger.error("Foundation order failed for %s: %s", symbol, e)
                    trade_record = {
                        "politician": signal.get("politician"),
                        "token": token,
                        "ticker": signal.get("ticker"),
                        "amount_usd": amount_usd,
                        "side": "BUY",
                        "status": "FAILED",
                        "tag": "foundation",
                        "error": str(e),
                    }
                    results.append(trade_record)

        logger.info(
            "Foundation execution complete: %d signals processed, %d results",
            len(signals), len(results),
        )
        return results
