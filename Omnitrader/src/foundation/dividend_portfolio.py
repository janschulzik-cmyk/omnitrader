"""Dividend Portfolio Manager for Foundation Module.

Maintains a basket of high-yield assets, monitors dividends,
and manages DeFi staking positions.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx
import yaml

from ..utils.db import DividendHolding, get_session, log_system_event
from ..utils.logging_config import get_logger

logger = get_logger("foundation.dividend")


class DividendPortfolio:
    """Manages a portfolio of high-dividend assets with DRIP."""

    def __init__(
        self,
        config: Dict = None,
        token_map_path: str = None,
    ):
        """Initialize the dividend portfolio.

        Args:
            config: Configuration with asset list and weights.
            token_map_path: Path to token_map.yaml for crypto mappings.
        """
        self.config = config or {}
        self.assets = self.config.get("dividend_assets", [])
        self.deci_drip = self.config.get("dividend_reinvest", True)
        self.token_map_path = token_map_path or "config/token_map.yaml"
        self.token_map = self._load_token_map()

        # DeFi configuration
        self.defi_enabled = self.config.get("defi", {}).get("enabled", False)
        self.aave_pool_url = self.config.get("defi", {}).get("aave_pool_url", "")
        self.lido_stake_url = self.config.get("defi", {}).get("lido_stake_url", "")
        self.defi_wallet_key = os.environ.get("DEFI_WALLET_KEY", "")

        # Track holdings
        self.holdings: Dict[str, Dict] = {}
        self._load_holdings()

        # Dividend payout monitoring
        self.payout_ratios: Dict[str, float] = {}

    def _load_token_map(self) -> Dict:
        """Load the token mapping file.

        Returns:
            Dict mapping asset info to tokens.
        """
        try:
            if os.path.exists(self.token_map_path):
                with open(self.token_map_path, "r") as f:
                    data = yaml.safe_load(f)
                    return data.get("mapping", {})
        except Exception as e:
            logger.error("Failed to load token map: %s", e)
        return {}

    def _load_holdings(self) -> None:
        """Load current holdings from database."""
        from ..utils.db import DividendHolding
        session = get_session()
        try:
            records = session.query(DividendHolding).all()
            for record in records:
                self.holdings[record.ticker] = {
                    "quantity": record.quantity,
                    "avg_cost": record.avg_cost,
                    "last_reinvest": record.last_reinvest,
                }
        finally:
            session.close()

    def get_current_prices(self, tickers: List[str] = None) -> Dict[str, float]:
        """Get current prices for assets using Yahoo Finance API.

        Args:
            tickers: List of ticker symbols.

        Returns:
            Dict mapping tickers to current prices.
        """
        if tickers is None:
            tickers = [a["ticker"] for a in self.assets]

        prices = {}
        for ticker in tickers:
            price = self._fetch_yahoo_price(ticker)
            if price:
                prices[ticker] = price

        return prices

    def _fetch_yahoo_price(self, ticker: str) -> Optional[float]:
        """Fetch current price from Yahoo Finance.

        Args:
            ticker: Ticker symbol.

        Returns:
            Current price, or None on failure.
        """
        try:
            # Using Yahoo Finance API via yfinance would be ideal,
            # but for simplicity we use a direct HTTP approach
            response = httpx.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"range": "1d", "interval": "1d"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            result = data.get("chart", {}).get("result", [{}])
            if result:
                meta = result[0].get("meta", {})
                return meta.get("regularMarketPrice", 0)
        except Exception as e:
            logger.warning("Failed to fetch price for %s: %s", ticker, e)
        return None

    def get_payout_ratio(self, ticker: str) -> Optional[float]:
        """Get the dividend payout ratio for a stock.

        Args:
            ticker: Ticker symbol.

        Returns:
            Payout ratio as a float, or None on failure.
        """
        try:
            response = httpx.get(
                f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
                params={"modules": "defaultKeyStatistics,financialData"},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            summary = data.get("quoteSummary", {}).get("result", [{}])

            if summary:
                financial_data = summary[0].get("financialData", {})
                payout = financial_data.get("payoutRatio")
                if payout is not None:
                    self.payout_ratios[ticker] = payout
                    return payout
        except Exception as e:
            logger.warning("Failed to fetch payout ratio for %s: %s", ticker, e)

        # Return a reasonable default if fetch fails
        defaults = {
            "ARCC": 0.55, "SDIV": 0.40, "O": 0.75,
            "ENB": 0.60, "ABBV": 0.45,
        }
        return defaults.get(ticker)

    def check_dividend_flags(self) -> List[Dict]:
        """Check all dividend assets for red flags.

        Returns:
            List of flagged assets with issues.
        """
        flags = []
        for asset in self.assets:
            ticker = asset["ticker"]
            payout = self.get_payout_ratio(ticker)

            if payout is not None and payout > 1.0:
                flags.append({
                    "ticker": ticker,
                    "issue": "EXCESSIVE_PAYOUT",
                    "payout_ratio": payout,
                    "message": f"Dividend payout ratio {payout:.0%} exceeds 100%",
                })

        if flags:
            logger.warning("Found %d dividend red flags", len(flags))
        return flags

    def calculate_portfolio_value(self, prices: Dict[str, float]) -> float:
        """Calculate total portfolio value.

        Args:
            prices: Dict of ticker -> current price.

        Returns:
            Total portfolio value in USD.
        """
        total = 0.0
        for ticker, price in prices.items():
            if ticker in self.holdings:
                holding = self.holdings[ticker]
                total += holding["quantity"] * price
        return total

    def reinvest_dividends(self, dividend_events: List[Dict]) -> List[Dict]:
        """Reinvest dividends back into the portfolio (DRIP).

        Args:
            dividend_events: List of dividend payment events.

        Returns:
            List of reinvestment records.
        """
        if not self.deci_drip:
            logger.info("DRIP disabled. Skipping reinvestment.")
            return []

        reinvestments = []
        session = get_session()
        try:
            for event in dividend_events:
                ticker = event.get("ticker", "")
                amount = event.get("amount", 0)
                price = event.get("price", 0)

                if price <= 0:
                    continue

                shares_to_buy = amount / price

                if ticker in self.holdings:
                    # Update existing holding
                    current = self.holdings[ticker]
                    new_qty = current["quantity"] + shares_to_buy
                    new_avg_cost = (
                        (current["quantity"] * current["avg_cost"] + shares_to_buy * price)
                        / new_qty
                    )
                    self.holdings[ticker] = {
                        "quantity": new_qty,
                        "avg_cost": new_avg_cost,
                        "last_reinvest": datetime.utcnow().isoformat(),
                    }
                else:
                    self.holdings[ticker] = {
                        "quantity": shares_to_buy,
                        "avg_cost": price,
                        "last_reinvest": datetime.utcnow().isoformat(),
                    }

                # Save to database
                holding = DividendHolding(
                    ticker=ticker,
                    quantity=self.holdings[ticker]["quantity"],
                    avg_cost=self.holdings[ticker]["avg_cost"],
                    last_reinvest=datetime.utcnow(),
                )
                session.add(holding)

                reinvestments.append({
                    "ticker": ticker,
                    "shares": shares_to_buy,
                    "price": price,
                    "amount_reinvested": amount,
                })

            session.commit()
            logger.info("Reinvested %d dividends", len(reinvestments))
            return reinvestments

        except Exception as e:
            session.rollback()
            logger.error("Failed to reinvest dividends: %s", e)
            return []
        finally:
            session.close()

    def get_portfolio_summary(self) -> Dict:
        """Get a summary of the current portfolio.

        Returns:
            Dict with portfolio details.
        """
        prices = self.get_current_prices([a["ticker"] for a in self.assets])
        flagged = self.check_dividend_flags()

        holdings_list = []
        for ticker, info in self.holdings.items():
            price = prices.get(ticker, 0)
            holdings_list.append({
                "ticker": ticker,
                "quantity": info["quantity"],
                "avg_cost": info["avg_cost"],
                "current_price": price,
                "market_value": info["quantity"] * price,
                "unrealized_pnl": (price - info["avg_cost"]) * info["quantity"],
            })

        total_value = self.calculate_portfolio_value(prices)

        return {
            "total_value": round(total_value, 2),
            "holdings": holdings_list,
            "dividend_flags": flagged,
            "drip_enabled": self.deci_drip,
            "last_updated": datetime.utcnow().isoformat(),
        }

    def fetch_defi_yields(self) -> Dict:
        """Fetch current DeFi yield rates from Aave and Lido.

        Returns:
            Dict with yield rates for Aave stablecoins and Lido ETH.
        """
        if not self.defi_enabled:
            return {"enabled": False}

        yields = {"enabled": True, "aave_usdc": 0, "lido_eth_apr": 0}

        try:
            # Aave USDC rate (simplified)
            if self.aave_pool_url:
                response = httpx.get(
                    f"{self.aave_pool_url}/USDC",
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                yields["aave_usdc"] = float(data.get("rate", 0))
        except Exception as e:
            logger.warning("Failed to fetch Aave rates: %s", e)

        try:
            # Lido ETH staking APR
            if self.lido_stake_url:
                response = httpx.get(self.lido_stake_url, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                yields["lido_eth_apr"] = float(data.get("apr", 0))
        except Exception as e:
            logger.warning("Failed to fetch Lido rates: %s", e)

        logger.info("DeFi yields: %s", yields)
        return yields
