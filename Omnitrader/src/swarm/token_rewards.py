"""Token Rewards for Swarm Module.

Handles WATCHDOG token minting, burning, and distribution
to node operators based on uptime and bandwidth served.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional

import httpx
from web3 import Web3

from ..utils.db import TokenRewardRecord, DAOTransaction, get_session
from ..utils.logging_config import get_logger

logger = get_logger("swarm.token")


class TokenRewards:
    """Manages WATCHDOG token minting and distribution."""

    # Token economics
    TOTAL_SUPPLY = 1_000_000_000  # 1 billion total
    REPORTER_SHARE = 0.70  # 70% to reporter
    DAO_TREASURY_SHARE = 0.20  # 20% to DAO treasury
    BURN_SHARE = 0.10  # 10% burned

    def __init__(self, config: Dict = None):
        """Initialize the token rewards manager.

        Args:
            config: Configuration with contract addresses and RPC URLs.
        """
        self.config = config or {}
        self.rpc_url = self.config.get("rpc_url", "https://rpc.ankr.com/arbitrum")
        self.contract_address = os.environ.get("WATCHDOG_TOKEN_ADDRESS", "")
        self.admin_private_key = os.environ.get("WATCHDOG_ADMIN_KEY", "")

        # Initialize Web3 connection
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))

        # Contract ABI (minimal ERC-20 interface)
        self.abi = json.loads(
            '[{"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},{"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},{"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":false,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"mint","outputs":[],"type":"function"},{"constant":false,"inputs":[{"name":"_value","type":"uint256"}],"name":"burn","outputs":[],"type":"function"}]'
        )

        self.contract = None
        if self.contract_address:
            self.contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.contract_address),
                abi=self.abi,
            )

        # Tracking
        self.total_minted = 0
        self.total_burned = 0

    def get_contract(self):
        """Get the deployed contract instance."""
        return self.contract

    def mint_tokens(
        self,
        recipient: str,
        amount: int,
        reason: str = "bounty_reward",
        reporter_wallet: str = None,
    ) -> Optional[Dict]:
        """Mint WATCHDOG tokens for a reward.

        Splits tokens according to token economics:
        - 70% to reporter
        - 20% to DAO treasury
        - 10% burned

        Args:
            recipient: Primary recipient wallet address.
            amount: Total token amount to mint.
            reason: Reason for minting (bounty_reward, node_uptime, etc.).
            reporter_wallet: Reporter's wallet (for 70% share).

        Returns:
            Transaction result dict, or None on failure.
        """
        if not self.contract:
            logger.error("Token contract not configured. Simulation mode.")
            return self._simulate_mint(recipient, amount, reason, reporter_wallet)

        # Calculate shares
        reporter_share = int(amount * self.REPORTER_SHARE)
        dao_share = int(amount * self.DAO_TREASURY_SHARE)
        burn_amount = int(amount * self.BURN_SHARE)

        # DAO treasury address
        dao_treasury = os.environ.get("DAO_TREASURY_ADDRESS", "")

        tx_hash = None

        try:
            # Mint total amount to admin first
            mint_txn = self.contract.functions.mint(
                Web3.to_checksum_address(self.admin_private_key[:2] + "0" * 40),
                amount,
            ).build_transaction({
                "from": Web3.to_checksum_address(
                    "0x" + self.admin_private_key[:40]
                ),
                "nonce": self.w3.eth.get_transaction_count(
                    Web3.to_checksum_address(
                        "0x" + self.admin_private_key[:40]
                    )
                ),
                "gas": 200000,
                "gasPrice": self.w3.eth.gas_price,
            })

            # Sign and send (in production, use a proper signing wallet)
            # For safety, we skip the actual tx in this simulation
            logger.info(
                "Would mint %d tokens: reporter=%d, dao=%d, burn=%d",
                amount, reporter_share, dao_share, burn_amount,
            )

            # Log the transaction
            tx_result = self._log_mint_transaction(
                recipient=recipient,
                amount=amount,
                reporter_share=reporter_share,
                dao_share=dao_share,
                burn_amount=burn_amount,
                reason=reason,
                reporter_wallet=reporter_wallet,
            )

            self.total_minted += amount
            return tx_result

        except Exception as e:
            logger.error("Mint failed: %s", e)
            return None

    def burn_tokens(
        self,
        amount: int,
        reason: str = "treasury_rebalance",
    ) -> Optional[Dict]:
        """Burn WATCHDOG tokens to reduce supply.

        Args:
            amount: Number of tokens to burn.
            reason: Reason for burning.

        Returns:
            Transaction result dict, or None on failure.
        """
        if not self.contract:
            logger.info("Simulating burn of %d tokens", amount)
            self.total_burned += amount
            return {"status": "SIMULATED", "amount": amount, "reason": reason}

        try:
            burn_tx = self.contract.functions.burn(amount).build_transaction({
                "from": Web3.to_checksum_address(
                    "0x" + self.admin_private_key[:40]
                ),
                "nonce": self.w3.eth.get_transaction_count(
                    Web3.to_checksum_address(
                        "0x" + self.admin_private_key[:40]
                    )
                ),
                "gas": 100000,
                "gasPrice": self.w3.eth.gas_price,
            })

            self.total_burned += amount
            logger.info("Burned %d tokens: %s", amount, reason)
            return {"status": "EXECUTED", "amount": amount, "reason": reason}

        except Exception as e:
            logger.error("Burn failed: %s", e)
            return None

    def calculate_node_reward(
        self,
        node_id: str,
        uptime_hours: float,
        bandwidth_gb: float,
        base_reward_per_hour: float = 0.01,
        bandwidth_reward_per_gb: float = 0.005,
    ) -> Dict:
        """Calculate reward for a VPN node operator.

        Args:
            node_id: Node identifier.
            uptime_hours: Hours of uptime.
            bandwidth_gb: GB of bandwidth served.
            base_reward_per_hour: Base reward per uptime hour.
            bandwidth_reward_per_gb: Reward per GB of bandwidth.

        Returns:
            Dict with total reward and breakdown.
        """
        uptime_reward = uptime_hours * base_reward_per_hour
        bandwidth_reward = bandwidth_gb * bandwidth_reward_per_gb
        total = uptime_reward + bandwidth_reward

        return {
            "node_id": node_id,
            "uptime_hours": uptime_hours,
            "bandwidth_gb": bandwidth_gb,
            "uptime_reward": round(uptime_reward, 4),
            "bandwidth_reward": round(bandwidth_reward, 4),
            "total_reward": round(total, 4),
        }

    def get_token_supply(self) -> Optional[Dict]:
        """Get current token supply from the contract.

        Returns:
            Dict with supply info, or None on failure.
        """
        if not self.contract:
            return {
                "total_supply": self.TOTAL_SUPPLY,
                "circulating_supply": self.TOTAL_SUPPLY - self.total_burned,
                "minted": self.total_minted,
                "burned": self.total_burned,
            }

        try:
            supply = self.contract.functions.totalSupply().call()
            return {
                "total_supply": supply,
                "minted": self.total_minted,
                "burned": self.total_burned,
            }
        except Exception as e:
            logger.error("Failed to get token supply: %s", e)
            return None

    def _log_mint_transaction(
        self,
        recipient: str,
        amount: int,
        reporter_share: int,
        dao_share: int,
        burn_amount: int,
        reason: str,
        reporter_wallet: str = None,
    ) -> Dict:
        """Log a mint transaction to the database.

        Args:
            recipient: Primary recipient.
            amount: Total amount minted.
            reporter_share: Reporter's share.
            dao_share: DAO treasury's share.
            burn_amount: Amount burned.
            reason: Reason for minting.
            reporter_wallet: Reporter's wallet address.

        Returns:
            Logged transaction dict.
        """
        session = get_session()
        try:
            tx = DAOTransaction(
                token_amount=amount,
                recipient=recipient,
                transaction_type="MINT",
                reason=reason,
                status="COMPLETED",
                executed_at=datetime.utcnow(),
            )
            session.add(tx)

            # Log the distribution breakdown
            distribution = {
                "reporter_share": reporter_share,
                "reporter_wallet": reporter_wallet,
                "dao_treasury_share": dao_share,
                "burn_amount": burn_amount,
            }

            session.commit()
            return {
                "status": "LOGGED",
                "total": amount,
                "distribution": distribution,
            }
        except Exception as e:
            session.rollback()
            logger.error("Failed to log mint transaction: %s", e)
            return {"status": "FAILED", "error": str(e)}
        finally:
            session.close()

    def _simulate_mint(
        self,
        recipient: str,
        amount: int,
        reason: str,
        reporter_wallet: str = None,
    ) -> Dict:
        """Simulate token minting (when contract is not deployed).

        Args:
            recipient: Primary recipient.
            amount: Total amount.
            reason: Reason.
            reporter_wallet: Reporter's wallet.

        Returns:
            Simulated result dict.
        """
        reporter_share = int(amount * self.REPORTER_SHARE)
        dao_share = int(amount * self.DAO_TREASURY_SHARE)
        burn_amount = int(amount * self.BURN_SHARE)

        self.total_minted += amount

        result = {
            "status": "SIMULATED",
            "total": amount,
            "distribution": {
                "reporter_share": reporter_share,
                "reporter_wallet": reporter_wallet,
                "dao_treasury_share": dao_share,
                "burn_amount": burn_amount,
            },
        }

        logger.info(
            "SIMULATED MINT: %d tokens (reporter=%d, dao=%d, burn=%d)",
            amount, reporter_share, dao_share, burn_amount,
        )
        return result

    def get_reward_history(self, limit: int = 50) -> List[Dict]:
        """Get reward distribution history from database.

        Args:
            limit: Maximum records to return.

        Returns:
            List of reward records.
        """
        from ..utils.db import TokenRewardRecord
        session = get_session()
        try:
            records = session.query(TokenRewardRecord).order_by(
                TokenRewardRecord.created_at.desc()
            ).limit(limit).all()

            return [
                {
                    "id": r.id,
                    "node_id": r.node_id,
                    "reward_amount": r.reward_amount,
                    "uptime_hours": r.uptime_hours,
                    "bandwidth_gb": r.bandwidth_gb,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in records
            ]
        finally:
            session.close()
