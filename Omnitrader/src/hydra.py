"""
Hydra: Capital Pool Management

Manages three virtual capital pools (Moat, StrikerPool, FoundationPool)
with profit distribution, balance reconciliation, and persistence in SQLite.
"""

import os
from datetime import datetime
from typing import Dict, Optional

import yaml

from .utils.db import (
    get_session,
    PoolBalance,
    create_initial_pools,
)
from .utils.logging_config import get_logger

logger = get_logger("hydra")


class Hydra:
    """
    Capital allocation engine that manages three virtual pool balances.

    Pools:
        - moat: Safe cash reserve (10% of total capital), held as USDC
        - striker: Active trading capital (70% of total capital)
        - foundation: Long-term portfolio (20% of total capital)
    """

    def __init__(self, config_path: str = None, *, striker: float = None, foundation: float = None, moat: float = None, config: dict = None):
        """
        Initialize the Hydra capital manager.

        Args:
            config_path: Path to config/settings.yaml. If None, searches
                         up the directory tree for config/settings.yaml.
            striker: Pre-computed striker capital (for load()).
            foundation: Pre-computed foundation capital (for load()).
            moat: Pre-computed moat capital (for load()).
            config: Pre-loaded config dict (for load()).
        """
        if config is not None:
            # Called from load() with pre-computed values
            self.config = config
            self._profit_split = config.get("capital", {}).get("profit_split", {})
            total = float(os.environ.get("TOTAL_INITIAL_CAPITAL", 100))
            self.total_capital = total
            self.striker = striker
            self.foundation = foundation
            self.moat = moat
        else:
            self.config = self._load_config(config_path)
            self._profit_split = self.config.get("capital", {}).get("profit_split", {})

            # Total capital from environment or config
            self.total_capital = float(
                os.environ.get("TOTAL_INITIAL_CAPITAL", "100.00")
            )

            # Initialize database and pools
            from .utils.db import init_db
            db_url = os.environ.get(
                "DATABASE_URL",
                self.config.get("database", {}).get("default", "sqlite:///data/omnitrader.db")
            )
            init_db(db_url)
            create_initial_pools(
                total_capital=self.total_capital,
                moat_ratio=self.config["capital"]["moat_ratio"],
                foundation_ratio=self.config["capital"]["foundation_ratio"],
                striker_ratio=self.config["capital"]["striker_ratio"],
            )

            self.striker = self.total_capital * self.config["capital"]["striker_ratio"]
            self.foundation = self.total_capital * self.config["capital"]["foundation_ratio"]
            self.moat = self.total_capital * self.config["capital"]["moat_ratio"]

        logger.info(
            "Hydra initialized: total=%.2f (moat=%.2f, foundation=%.2f, striker=%.2f)",
            self.total_capital,
            self.moat,
            self.foundation,
            self.striker,
        )

    @classmethod
    def load(cls, config_path: str = None, *, config: dict = None) -> "Hydra":
        import os
        import yaml

        if config is not None and isinstance(config, dict):
            # Pre-loaded config dict passed directly (e.g. from main.py)
            config_data = config
        else:
            # Load from YAML file
            if not config_path:
                config_path = os.environ.get(
                    "HYDRA_CONFIG",
                    os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "settings.yaml")
                )

            with open(config_path) as f:
                config_data = yaml.safe_load(f)

        total = float(os.environ.get("TOTAL_INITIAL_CAPITAL", 100))
        caps = config_data.get("capital", {})
        striker = total * caps.get("striker_ratio", 0.7)
        foundation = total * caps.get("foundation_ratio", 0.2)
        moat = total * caps.get("moat_ratio", 0.1)

        return cls(striker=striker, foundation=foundation, moat=moat, config=config_data)

    def get_status(self) -> dict:
        return {
            "striker": self.striker,
            "foundation": self.foundation,
            "moat": self.moat
        }

    def initialize_pools(self, total_capital: float) -> None:
        """Initialize database and pool balances for pre-loaded Hydra instances."""
        from .utils.db import init_db, create_initial_pools
        db_url = os.environ.get(
            "DATABASE_URL",
            self.config.get("database", {}).get("default", "sqlite:///data/omnitrader.db")
        )
        init_db(db_url)
        create_initial_pools(
            total_capital=total_capital,
            moat_ratio=self.config["capital"]["moat_ratio"],
            foundation_ratio=self.config["capital"]["foundation_ratio"],
            striker_ratio=self.config["capital"]["striker_ratio"],
        )

    @staticmethod
    def _load_config(config_path: str = None) -> dict:
        """
        Load configuration from settings.yaml.

        Args:
            config_path: Explicit path to config file.

        Returns:
            Parsed YAML configuration dict.
        """
        if config_path and os.path.exists(config_path):
            path = config_path
        else:
            # Search for config in parent directories
            current = os.path.dirname(os.path.abspath(__file__))
            while current != "/":
                candidate = os.path.join(current, "config", "settings.yaml")
                if os.path.exists(candidate):
                    path = candidate
                    break
                current = os.path.dirname(current)
            else:
                path = "config/settings.yaml"

        with open(path, "r") as f:
            return yaml.safe_load(f)

    def get_balance(self, pool: str) -> float:
        """
        Get the current balance for a pool.

        Args:
            pool: Pool name ('moat', 'striker', or 'foundation').

        Returns:
            Current balance as float.
        """
        pool = pool.lower()
        valid_pools = ("moat", "striker", "foundation")
        if pool not in valid_pools:
            raise ValueError(f"Unknown pool '{pool}'. Valid pools: {valid_pools}")

        session = get_session()
        try:
            record = session.query(PoolBalance).filter_by(pool_name=pool).first()
            if record is None:
                logger.warning("Pool '%s' not found in database.", pool)
                return 0.0
            return record.balance
        finally:
            session.close()

    def update_balance(self, pool: str, amount: float) -> float:
        """
        Atomically update a pool balance.

        Args:
            pool: Pool name ('moat', 'striker', 'foundation').
            amount: New absolute balance value.

        Returns:
            The new balance value.
        """
        pool = pool.lower()
        valid_pools = ("moat", "striker", "foundation", "dao_treasury")
        if pool not in valid_pools:
            raise ValueError(f"Unknown pool '{pool}'. Valid pools: {valid_pools}")

        session = get_session()
        try:
            record = session.query(PoolBalance).filter_by(pool_name=pool).first()
            if record is None:
                raise ValueError(f"Pool '{pool}' does not exist in database.")

            old_balance = record.balance
            record.balance = amount
            record.updated_at = datetime.utcnow()

            delta = amount - old_balance
            if delta > 0:
                record.total_profit += delta
            elif delta < 0:
                record.total_withdrawn += abs(delta)

            session.commit()
            logger.info(
                "Pool '%s' updated: %.2f -> %.2f (delta=%.2f)",
                pool, old_balance, amount, delta,
            )
            return amount
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def apply_profit(self, profit: float) -> Dict[str, float]:
        """
        Distribute profits from a closed trade across pools.

        Profit split:
            - striker_keep: % goes back to Striker pool
            - moat: % goes to Moat (safe reserve)
            - foundation: % goes to Foundation pool

        Args:
            profit: The profit amount from a closed trade (must be positive).

        Returns:
            Dict mapping pool name to amount credited.
        """
        if profit <= 0:
            logger.warning("apply_profit called with non-positive profit: %.2f. Ignoring.", profit)
            return {}

        striker_share = profit * self._profit_split.get("striker_keep", 0.5)
        moat_share = profit * self._profit_split.get("moat", 0.25)
        foundation_share = profit * self._profit_split.get("foundation", 0.25)

        # Verify split sums to 1.0
        total_split = striker_share + moat_share + foundation_share
        if abs(total_split - profit) > 0.0001:
            logger.warning(
                "Profit split does not sum to 1.0: %.4f. Adjusting last share.",
                total_split,
            )
            foundation_share = profit - striker_share - moat_share

        updates = {
            "striker": self.update_balance("striker", self.get_balance("striker") + striker_share),
            "moat": self.update_balance("moat", self.get_balance("moat") + moat_share),
            "foundation": self.update_balance("foundation", self.get_balance("foundation") + foundation_share),
        }

        logger.info(
            "Profit distributed: profit=%.2f -> striker=%.2f, moat=%.2f, foundation=%.2f",
            profit, striker_share, moat_share, foundation_share,
        )
        return updates

    def apply_loss(self, loss: float, pool: str = "striker") -> float:
        """
        Deduct a loss from a pool balance.

        Args:
            loss: Loss amount (must be positive).
            pool: Which pool to deduct from.

        Returns:
            The new balance.
        """
        if loss <= 0:
            logger.warning("apply_loss called with non-positive loss: %.2f. Ignoring.", loss)
            return self.get_balance(pool)

        current = self.get_balance(pool)
        new_balance = current - loss
        if new_balance < 0:
            logger.error(
                "Cannot deduct %.2f loss from '%s' pool: balance %.2f < loss. Capping at 0.",
                loss, pool, current,
            )
            new_balance = 0.0

        return self.update_balance(pool, new_balance)

    def can_trade(self, striker_capital: float = None) -> bool:
        """
        Check if the Striker pool has sufficient capital for trading.

        Args:
            striker_capital: Current striker balance. If None, reads from DB.

        Returns:
            True if Striker pool has at least 1% of total capital.
        """
        if striker_capital is None:
            striker_capital = self.get_balance("striker")

        min_capital = self.total_capital * 0.01  # Minimum 1% of total
        return striker_capital >= min_capital

    def reconcile(self) -> Dict[str, float]:
        """
        Daily reconciliation: verify DB balances match exchange balances.

        NOTE: In production, this would compare DB balances against actual
        exchange API balances and the blockchain explorer for Moat.

        Returns:
            Dict of current pool balances after reconciliation.
        """
        logger.info("Starting daily pool reconciliation...")
        balances = {}
        for pool_name in ("moat", "striker", "foundation"):
            balance = self.get_balance(pool_name)
            balances[pool_name] = balance
            logger.info("  %s pool: %.2f", pool_name, balance)

        logger.info("Reconciliation complete. Balances: %s", balances)
        return balances

    def update_moat_from_blockchain(self, etherscan_api_key: str = None) -> float:
        """
        Update Moat balance by reading from blockchain explorer.

        In production, this queries Etherscan API for the USDC balance
        of the Moat wallet address. For now, uses the DB balance.

        Args:
            etherscan_api_key: Etherscan API key for balance queries.

        Returns:
            Current Moat balance.
        """
        # In production:
        # wallet = get_wallet_address()
        # balance_wei = etherscan_api.get_token_balance(
        #     contract="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
        #     address=wallet,
        #     api_key=etherscan_api_key,
        # )
        # balance_usdc = balance_wei / 10**6
        # self.update_balance("moat", balance_usdc)

        balance = self.get_balance("moat")
        logger.info("Moat balance (blockchain read): %.2f USDC", balance)
        return balance

    def get_all_balances(self) -> Dict[str, float]:
        """
        Get all pool balances in a single call.

        Returns:
            Dict mapping pool name to current balance.
        """
        return {
            "moat": self.get_balance("moat"),
            "striker": self.get_balance("striker"),
            "foundation": self.get_balance("foundation"),
        }

    def get_pool_details(self) -> Dict[str, dict]:
        """
        Get detailed pool information including totals.

        Returns:
            Dict with pool name as key and balance details as value.
        """
        session = get_session()
        try:
            results = {}
            for pool_name in ("moat", "striker", "foundation"):
                record = session.query(PoolBalance).filter_by(pool_name=pool_name).first()
                if record:
                    results[pool_name] = {
                        "balance": record.balance,
                        "total_deposited": record.total_deposited,
                        "total_withdrawn": record.total_withdrawn,
                        "total_profit": record.total_profit,
                        "updated_at": record.updated_at.isoformat(),
                    }
            return results
        finally:
            session.close()


# Module-level singleton
_hydra_instance = None


def get_hydra(config_path: str = None) -> Hydra:
    """
    Get or create the singleton Hydra instance.

    Args:
        config_path: Path to config file.

    Returns:
        Hydra singleton instance.
    """
    global _hydra_instance
    if _hydra_instance is None:
        _hydra_instance = Hydra(config_path=config_path)
    return _hydra_instance
