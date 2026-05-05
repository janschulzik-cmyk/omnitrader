"""Portfolio Rebalancer for Foundation Module.

Computes target weight deviations and generates trades to
rebalance the portfolio weekly.
"""

from datetime import datetime
from typing import Dict, List, Optional

from ..utils.db import get_session, log_system_event
from ..utils.logging_config import get_logger

logger = get_logger("foundation.rebalancer")


class Rebalancer:
    """Rebalances portfolio weights to match targets."""

    def __init__(self, config: Dict = None):
        """Initialize the rebalancer.

        Args:
            config: Configuration with target weights and slippage tolerance.
        """
        self.config = config or {}
        self.target_weights = self.config.get("target_weights", {})
        self.slippage_tolerance = self.config.get("slippage_tolerance", 0.01)  # 1%
        self.min_rebalance_threshold = self.config.get(
            "min_rebalance_threshold", 0.05  # 5% deviation to trigger
        )

    def compute_current_weights(
        self, holdings: List[Dict]
    ) -> Dict[str, float]:
        """Compute current asset weights from holdings.

        Args:
            holdings: List of holding dicts with ticker, quantity, price.

        Returns:
            Dict mapping tickers to current weight percentages.
        """
        total_value = sum(h.get("market_value", 0) for h in holdings)
        if total_value <= 0:
            return {}

        weights = {}
        for holding in holdings:
            ticker = holding["ticker"]
            weights[ticker] = holding["market_value"] / total_value

        return weights

    def compute_deviations(
        self, current_weights: Dict[str, float]
    ) -> Dict[str, Dict]:
        """Compute weight deviations from targets.

        Args:
            current_weights: Current portfolio weights.

        Returns:
            Dict with deviation info for each asset.
        """
        deviations = {}
        for ticker, current_weight in current_weights.items():
            target_weight = self.target_weights.get(ticker, 0)
            deviation = current_weight - target_weight
            deviations[ticker] = {
                "current_weight": round(current_weight, 4),
                "target_weight": round(target_weight, 4),
                "deviation": round(deviation, 4),
                "needs_rebalance": abs(deviation) >= self.min_rebalance_threshold,
            }
        return deviations

    def generate_rebalance_trades(
        self,
        deviations: Dict[str, Dict],
        portfolio_value: float,
        prices: Dict[str, float],
    ) -> List[Dict]:
        """Generate trades to rebalance the portfolio.

        Args:
            deviations: Deviation dict from compute_deviations.
            portfolio_value: Total portfolio value.
            prices: Dict of ticker -> current price.

        Returns:
            List of trade dicts for rebalancing.
        """
        trades = []

        for ticker, dev_info in deviations.items():
            if not dev_info["needs_rebalance"]:
                continue

            target_value = portfolio_value * dev_info["target_weight"]
            current_value = portfolio_value * dev_info["current_weight"]
            adjustment = target_value - current_value

            price = prices.get(ticker, 0)
            if price <= 0:
                logger.warning("No price for %s. Skipping.", ticker)
                continue

            quantity = abs(adjustment) / price

            # Apply slippage tolerance
            adjusted_price = price * (1 + self.slippage_tolerance) if adjustment > 0 else price * (1 - self.slippage_tolerance)
            adjusted_quantity = abs(adjustment) / adjusted_price

            trade = {
                "ticker": ticker,
                "action": "BUY" if adjustment > 0 else "SELL",
                "quantity": round(adjusted_quantity, 8),
                "price": round(price, 4),
                "estimated_value": round(abs(adjustment), 2),
                "current_weight": dev_info["current_weight"],
                "target_weight": dev_info["target_weight"],
                "deviation": dev_info["deviation"],
            }
            trades.append(trade)

        logger.info("Generated %d rebalance trades", len(trades))
        return trades

    def execute_rebalance(
        self,
        holdings: List[Dict],
        prices: Dict[str, float],
        foundation_pool: float,
    ) -> List[Dict]:
        """Full rebalancing workflow.

        Args:
            holdings: Current holdings.
            prices: Current market prices.
            foundation_pool: Foundation pool balance.

        Returns:
            List of executed trade records.
        """
        # Step 1: Compute current weights
        current_weights = self.compute_current_weights(holdings)

        # Step 2: Compute deviations
        deviations = self.compute_deviations(current_weights)

        # Step 3: Generate trades
        trades = self.generate_rebalance_trades(
            deviations, foundation_pool, prices
        )

        if not trades:
            logger.info("No rebalancing needed.")
            return []

        # Step 4: Log trades to database
        executed = self._log_rebalance_trades(trades)

        logger.info(
            "Rebalance complete: %d trades executed (portfolio value: $%.2f)",
            len(executed), foundation_pool,
        )
        return executed

    def _log_rebalance_trades(self, trades: List[Dict]) -> List[Dict]:
        """Log rebalance trades to the database.

        Args:
            trades: List of trade dicts.

        Returns:
            Same list with logged IDs.
        """
        from ..utils.db import RebalanceTrade
        session = get_session()
        try:
            for trade in trades:
                rebalance = RebalanceTrade(
                    ticker=trade["ticker"],
                    action=trade["action"],
                    quantity=trade["quantity"],
                    price=trade["price"],
                    estimated_value=trade["estimated_value"],
                    target_weight=trade["target_weight"],
                    executed_at=datetime.utcnow(),
                )
                session.add(rebalance)

            session.commit()
            return trades
        except Exception as e:
            session.rollback()
            logger.error("Failed to log rebalance trades: %s", e)
            return []
        finally:
            session.close()

    def get_rebalance_summary(self) -> Dict:
        """Get a summary of target weights and current status.

        Returns:
            Dict with rebalance configuration and status.
        """
        return {
            "target_weights": self.target_weights,
            "slippage_tolerance": self.slippage_tolerance,
            "min_rebalance_threshold": self.min_rebalance_threshold,
            "last_rebalance": datetime.utcnow().isoformat(),
        }
