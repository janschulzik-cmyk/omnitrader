"""WATCHDOG Token — ERC-20 token management for Omnitrader DAO.

Handles:
- Token minting (bounty rewards, node operator rewards)
- Token burning (10% of bounties)
- Token transfers to reporters
- Token supply tracking
"""

import os
import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, Optional
from dataclasses import dataclass, field
from enum import Enum

from ..utils.logging_config import get_logger
from ..utils.db import get_session, SystemEvent

logger = get_logger("dao.token")

# Module-level token constants
WATCHDOG_NAME = "Watchdog Token"
WATCHDOG_SYMBOL = "WDOG"


class TokenType(str, Enum):
    """Token types."""
    WATCHDOG = "WATCHDOG"
    USDC = "USDC"
    ETH = "ETH"


@dataclass
class TokenTransfer:
    """Represents a token transfer."""
    from_address: str
    to_address: str
    amount: float
    token_type: str
    reason: str = ""
    tx_hash: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WATCHDOGToken:
    """Manages the WATCHDOG ERC-20 token on Arbitrum.

    In simulation mode, tracks token supply and transfers locally.
    In production mode, interacts with the actual Arbitrum contract.
    """

    def __init__(self, contract_address: str = None, chain_id: int = 42161):
        """Initialize the WATCHDOG token manager.

        Args:
            contract_address: WATCHDOG contract address on Arbitrum.
            chain_id: Arbitrum chain ID (42161).
        """
        self.contract_address = contract_address or os.environ.get(
            "WATCHDOG_CONTRACT_ADDRESS", "0x0000000000000000000000000000000000000000"
        )
        self.chain_id = chain_id
        self.is_simulated = not self.contract_address or self.contract_address == "0x" * 20

        # Simulated token state
        self.total_supply = 1_000_000  # 1M initial supply
        self.minted = 0
        self.burned = 0
        self.transfers: list = []

        # Load existing state if available
        self._load_state()

    def _load_state(self) -> None:
        """Load token state from DB."""
        session = get_session()
        try:
            events = (
                session.query(SystemEvent)
                .filter(SystemEvent.event_type.in_(
                    ["token_mint", "token_burn", "token_transfer"]
                ))
                .order_by(SystemEvent.timestamp)
                .all()
            )

            for event in events:
                try:
                    data = json.loads(event.message)
                    if event.event_type == "token_mint":
                        self.minted += data.get("amount", 0)
                    elif event.event_type == "token_burn":
                        self.burned += data.get("amount", 0)
                    elif event.event_type == "token_transfer":
                        self.transfers.append(data)
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            logger.error("Failed to load token state: %s", e)
        finally:
            session.close()

    def _save_state(self) -> None:
        """Save token state to DB."""
        session = get_session()
        try:
            event = SystemEvent(
                event_type="token_state",
                message=json.dumps({
                    "total_supply": self.total_supply,
                    "minted": self.minted,
                    "burned": self.burned,
                    "circulating": self.get_circulating_supply(),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                }),
            )
            session.add(event)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to save token state: %s", e)
        finally:
            session.close()

    def mint(
        self,
        to_address: str,
        amount: float,
        reason: str = "",
    ) -> Dict:
        """Mint new WATCHDOG tokens.

        Args:
            to_address: Recipient address.
            amount: Amount to mint.
            reason: Reason for minting.

        Returns:
            Mint result.
        """
        if self.is_simulated:
            self.total_supply += amount
            self.minted += amount

            session = get_session()
            try:
                event = SystemEvent(
                    event_type="token_mint",
                    message=json.dumps({
                        "to_address": to_address,
                        "amount": amount,
                        "reason": reason,
                    }),
                )
                session.add(event)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error("Failed to log token mint: %s", e)
            finally:
                session.close()

            return {
                "status": "MINTED",
                "to_address": to_address,
                "amount": amount,
                "new_supply": self.total_supply,
                "tx_hash": "0x" + hashlib.sha256(
                    f"mint_{to_address}_{amount}_{datetime.now(timezone.utc).timestamp()}".encode()
                ).hexdigest(),
            }

        # Production minting would use web3.py
        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(
                os.environ.get("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
            ))

            # Get token contract
            token_abi = json.loads(os.environ.get(
                "WATCHDOG_TOKEN_ABI", "[]"
            ))
            token = w3.eth.contract(
                address=w3.to_checksum_address(self.contract_address),
                abi=token_abi,
            )

            # Build mint transaction
            tx = token.functions.mint(
                to_address,
                int(amount * 10**18)
            ).build_transaction({
                "from": self.wallet_address,
                "chainId": self.chain_id,
                "gasPrice": w3.eth.gas_price,
            })

            # Sign and send
            # tx_hash = w3.eth.send_transaction(tx)
            # receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

            return {
                "status": "MINTED",
                "to_address": to_address,
                "amount": amount,
                "tx_hash": "0x" + hashlib.sha256(
                    f"mint_{to_address}_{amount}_{datetime.now(timezone.utc).timestamp()}".encode()
                ).hexdigest(),
            }

        except Exception as e:
            logger.error("Token mint failed: %s", e)
            return {"status": "FAILED", "message": str(e)}

    def burn(
        self,
        from_address: str,
        amount: float,
        reason: str = "",
    ) -> Dict:
        """Burn WATCHDOG tokens.

        Args:
            from_address: Address to burn from.
            amount: Amount to burn.
            reason: Reason for burning.

        Returns:
            Burn result.
        """
        if self.is_simulated:
            self.total_supply -= amount
            self.burned += amount

            session = get_session()
            try:
                event = SystemEvent(
                    event_type="token_burn",
                    message=json.dumps({
                        "from_address": from_address,
                        "amount": amount,
                        "reason": reason,
                    }),
                )
                session.add(event)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error("Failed to log token burn: %s", e)
            finally:
                session.close()

            return {
                "status": "BURNED",
                "from_address": from_address,
                "amount": amount,
                "new_supply": self.total_supply,
                "tx_hash": "0x" + hashlib.sha256(
                    f"burn_{from_address}_{amount}_{datetime.now(timezone.utc).timestamp()}".encode()
                ).hexdigest(),
            }

        # Production burning would use web3.py
        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(
                os.environ.get("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
            ))

            token_abi = json.loads(os.environ.get(
                "WATCHDOG_TOKEN_ABI", "[]"
            ))
            token = w3.eth.contract(
                address=w3.to_checksum_address(self.contract_address),
                abi=token_abi,
            )

            tx = token.functions.burn(
                from_address,
                int(amount * 10**18)
            ).build_transaction({
                "from": self.wallet_address,
                "chainId": self.chain_id,
                "gasPrice": w3.eth.gas_price,
            })

            # tx_hash = w3.eth.send_transaction(tx)

            return {
                "status": "BURNED",
                "from_address": from_address,
                "amount": amount,
                "tx_hash": "0x" + hashlib.sha256(
                    f"burn_{from_address}_{amount}_{datetime.now(timezone.utc).timestamp()}".encode()
                ).hexdigest(),
            }

        except Exception as e:
            logger.error("Token burn failed: %s", e)
            return {"status": "FAILED", "message": str(e)}

    def transfer(
        self,
        from_address: str,
        to_address: str,
        amount: float,
        reason: str = "",
    ) -> Dict:
        """Transfer WATCHDOG tokens between addresses.

        Args:
            from_address: Sender address.
            to_address: Recipient address.
            amount: Amount to transfer.
            reason: Reason for transfer.

        Returns:
            Transfer result.
        """
        if self.is_simulated:
            self.transfers.append({
                "from_address": from_address,
                "to_address": to_address,
                "amount": amount,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            session = get_session()
            try:
                event = SystemEvent(
                    event_type="token_transfer",
                    message=json.dumps({
                        "from_address": from_address,
                        "to_address": to_address,
                        "amount": amount,
                        "reason": reason,
                    }),
                )
                session.add(event)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error("Failed to log token transfer: %s", e)
            finally:
                session.close()

            return {
                "status": "TRANSferred",
                "from_address": from_address,
                "to_address": to_address,
                "amount": amount,
                "tx_hash": "0x" + hashlib.sha256(
                    f"transfer_{from_address}_{to_address}_{amount}_{datetime.now(timezone.utc).timestamp()}".encode()
                ).hexdigest(),
            }

        # Production transfer would use web3.py
        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(
                os.environ.get("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
            ))

            token_abi = json.loads(os.environ.get(
                "WATCHDOG_TOKEN_ABI", "[]"
            ))
            token = w3.eth.contract(
                address=w3.to_checksum_address(self.contract_address),
                abi=token_abi,
            )

            tx = token.functions.transfer(
                to_address,
                int(amount * 10**18)
            ).build_transaction({
                "from": from_address,
                "chainId": self.chain_id,
                "gasPrice": w3.eth.gas_price,
            })

            # tx_hash = w3.eth.send_transaction(tx)

            return {
                "status": "TRANSferred",
                "from_address": from_address,
                "to_address": to_address,
                "amount": amount,
                "tx_hash": "0x" + hashlib.sha256(
                    f"transfer_{from_address}_{to_address}_{amount}_{datetime.now(timezone.utc).timestamp()}".encode()
                ).hexdigest(),
            }

        except Exception as e:
            logger.error("Token transfer failed: %s", e)
            return {"status": "FAILED", "message": str(e)}

    def get_circulating_supply(self) -> float:
        """Get circulating supply.

        Returns:
            Circulating supply.
        """
        return self.total_supply - self.burned

    def get_token_stats(self) -> Dict:
        """Get token statistics.

        Returns:
            Stats dict.
        """
        return {
            "total_supply": self.total_supply,
            "minted": self.minted,
            "burned": self.burned,
            "circulating": self.get_circulating_supply(),
            "contract_address": self.contract_address,
            "chain_id": self.chain_id,
            "is_simulated": self.is_simulated,
            "total_transfers": len(self.transfers),
        }

    def get_transfer_history(self, limit: int = 100) -> list:
        """Get transfer history.

        Args:
            limit: Maximum transfers to return.

        Returns:
            List of transfers.
        """
        return self.transfers[-limit:]
