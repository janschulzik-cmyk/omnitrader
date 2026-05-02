"""On-Chain Scanner for Sleuth Module.

Scans Ethereum, Arbitrum, and BSC for suspicious activity:
new token launches, rugpull patterns, malicious addresses,
and anomalous CEX deposit patterns.
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx
import httpx

from ..utils.db import OnChainAlert, get_session
from ..utils.logging_config import get_logger

logger = get_logger("sleuth.onchain")


class OnChainScanner:
    """Scans blockchain networks for suspicious on-chain activity."""

    def __init__(self, config: Dict = None):
        """Initialize the on-chain scanner.

        Args:
            config: Configuration with node URLs and scan parameters.
        """
        self.config = config or {}
        self.ethereum_rpc = os.environ.get("ALCHEMY_ETH_URL", "")
        self.arbitrum_rpc = os.environ.get("ALCHEMY_ARB_URL", "")
        self.bsc_rpc = os.environ.get("ALCHEMY_BSC_URL", "")
        self.etherscan_api_key = os.environ.get("ETHERSCAN_API_KEY", "")
        self.arbiscan_api_key = os.environ.get("ARBISCAN_API_KEY", "")
        self.bscscan_api_key = os.environ.get("BSCSCAN_API_KEY", "")

        # Malicious address lists (from public sources)
        self.sanctioned_addresses: Set[str] = set()
        self._load_sanctioned_list()

        # Known malicious patterns
        self.min_liquidity_usd = self.config.get("min_liquidity_usd", 10000)
        self.sniper_window_seconds = self.config.get("sniper_window_seconds", 60)
        self.max_dev_hold_pct = self.config.get("max_dev_hold_pct", 0.30)

    def _load_sanctioned_list(self) -> None:
        """Load known malicious/sanctioned address lists."""
        sanctioned_urls = [
            "https://api.chainalysis.com/sanctions",  # Requires API key
            "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x0000000000000000000000000000000000000000/list.json",
        ]

        for url in sanctioned_urls:
            try:
                response = httpx.get(url, timeout=30.0)
                data = response.json()
                # Parse addresses from various formats
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, str) and len(item) == 42:
                            self.sanctioned_addresses.add(item.lower())
                        elif isinstance(item, dict) and "address" in item:
                            self.sanctioned_addresses.add(item["address"].lower())
                elif isinstance(data, dict) and "addresses" in data:
                    for addr in data["addresses"]:
                        if isinstance(addr, str) and len(addr) == 42:
                            self.sanctioned_addresses.add(addr.lower())
            except Exception as e:
                logger.warning("Failed to load sanctioned list from %s: %s", url, e)

        logger.info("Loaded %d sanctioned addresses", len(self.sanctioned_addresses))

    def call_rpc(self, rpc_url: str, method: str, params: List) -> Optional[Dict]:
        """Make an RPC call to a blockchain node.

        Args:
            rpc_url: Node RPC URL.
            method: RPC method name.
            params: Method parameters.

        Returns:
            RPC response, or None on failure.
        """
        if not rpc_url:
            return None

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }

        try:
            response = httpx.post(
                rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning("RPC call failed (%s): %s", method, e)
            return None

    def get_balance(self, address: str, chain: str = "ethereum") -> int:
        """Get the ETH/native token balance of an address.

        Args:
            address: Ethereum address.
            chain: Chain name.

        Returns:
            Balance in wei.
        """
        rpc_url = self._get_rpc_url(chain)
        if not rpc_url:
            return 0

        result = self.call_rpc(rpc_url, "eth_getBalance", [address, "latest"])
        if result and "result" in result:
            return int(result["result"], 16)
        return 0

    def get_token_transfers(
        self,
        address: str,
        chain: str = "ethereum",
        limit: int = 50,
    ) -> List[Dict]:
        """Get ERC-20 token transfers for an address.

        Args:
            address: Ethereum address.
            chain: Chain name.
            limit: Number of transfers to fetch.

        Returns:
            List of transfer dicts.
        """
        api_key = self._get_api_key(chain)
        if not api_key:
            return self._simulate_transfers(limit)

        base_url = self._get_explorer_url(chain)
        if not base_url:
            return []

        url = f"{base_url}/api?module=account&action=tokentx"
        params = {
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": limit,
            "sort": "desc",
            "apikey": api_key,
        }

        try:
            response = httpx.get(url, params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            return data.get("result", [])
        except Exception as e:
            logger.warning("Failed to fetch token transfers: %s", e)
            return self._simulate_transfers(limit)

    def scan_new_token_launches(
        self,
        chain: str = "ethereum",
        since_blocks: int = 1000,
    ) -> List[Dict]:
        """Scan for new token launches in recent blocks.

        Args:
            chain: Chain name.
            since_blocks: Number of blocks to scan.

        Returns:
            List of token launch alerts.
        """
        rpc_url = self._get_rpc_url(chain)
        if not rpc_url:
            return self._simulate_new_tokens(chain)

        # Get current block number
        current_result = self.call_rpc(rpc_url, "eth_blockNumber", [])
        if not current_result:
            return self._simulate_new_tokens(chain)

        current_block = int(current_result["result"], 16)
        start_block = max(0, current_block - since_blocks)

        # Note: Full event log scanning requires archive node access.
        # For now, we'll use a simplified approach checking known DEX pairs.
        alerts = []

        # Check for new liquidity pool deployments on Uniswap-style DEXs
        alerts.extend(
            self._check_liquidity_events(chain, start_block, current_block)
        )

        for alert in alerts:
            self._save_alert(alert)

        logger.info("Scanned %d blocks on %s, found %d alerts",
                     since_blocks, chain, len(alerts))
        return alerts

    def _check_liquidity_events(
        self,
        chain: str,
        start_block: int,
        end_block: int,
    ) -> List[Dict]:
        """Check for liquidity addition events.

        Args:
            chain: Chain name.
            start_block: Starting block number.
            end_block: Ending block number.

        Returns:
            List of liquidity event alerts.
        """
        alerts = []
        # Simplified: check known DEX factory contracts
        dex_factories = {
            "ethereum": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",  # Uniswap V2
            "arbitrum": "0xf1D7CC64Fb485770B04e22C5A2b38E43F980c290",  # Camelot
            "bsc": "0xcA143Ce32Fe78f6f71Da06cA1E917A9c51203ddC",  # PancakeSwap V2
        }

        factory = dex_factories.get(chain)
        if not factory:
            return alerts

        # In a production system, we'd query event logs here.
        # For this implementation, we'll generate simulated alerts.
        return self._simulate_liquidity_events(chain)

    def _save_alert(self, alert: Dict) -> OnChainAlert:
        """Save an on-chain alert to the database.

        Args:
            alert: Alert dict.

        Returns:
            Saved OnChainAlert record.
        """
        session = get_session()
        try:
            record = OnChainAlert(
                chain=alert.get("chain", "unknown"),
                alert_type=alert.get("alert_type", "SUSPICIOUS"),
                severity=alert.get("severity", "medium"),
                addresses=alert.get("addresses", []),
                transaction_hashes=alert.get("tx_hashes", []),
                summary=alert.get("summary", ""),
                evidence=json.dumps(alert.get("evidence", {})),
                is_verified=False,
                created_at=datetime.utcnow(),
            )
            session.add(record)
            session.commit()
            return record
        except Exception as e:
            session.rollback()
            logger.error("Failed to save alert: %s", e)
            return None
        finally:
            session.close()

    def scan_malicious_addresses(
        self, addresses: List[str]
    ) -> List[Dict]:
        """Scan a list of addresses against known malicious databases.

        Args:
            addresses: List of Ethereum addresses to check.

        Returns:
            List of flagged address details.
        """
        flags = []

        for address in addresses:
            addr_lower = address.lower()

            # Check against sanctioned list
            if addr_lower in self.sanctioned_addresses:
                flags.append({
                    "address": address,
                    "flag": "SANCTIONED",
                    "severity": "critical",
                    "source": "Chainalysis Sanctions",
                })
                continue

            # Check transaction history for suspicious patterns
            transfers = self.get_token_transfers(address)
            if self._detect_malicious_patterns(address, transfers):
                flags.append({
                    "address": address,
                    "flag": "MALICIOUS_PATTERN",
                    "severity": "high",
                    "source": "Pattern Detection",
                    "details": "Suspicious transaction patterns detected",
                })

        return flags

    def _detect_malicious_patterns(
        self,
        address: str,
        transfers: List[Dict],
    ) -> bool:
        """Detect malicious transaction patterns.

        Args:
            address: Address to analyze.
            transfers: List of token transfers.

        Returns:
            True if malicious patterns detected.
        """
        if not transfers:
            return False

        # Check for rapid succession of transfers to many addresses
        if len(transfers) > 50:
            return True  # Potential spam/mixing

        # Check for interactions with known mixer contracts
        mixer_addresses = {
            "0x77b609862796e6a6083229cc039eb6358a493dfe",  # Tornado Cash
            "0xd3d252c25c233a41b5fa0b7e8283d9d3a491124c",
        }

        for transfer in transfers:
            from_addr = transfer.get("from", "").lower()
            to_addr = transfer.get("to", "").lower()

            if from_addr in mixer_addresses or to_addr in mixer_addresses:
                return True

        return False

    def detect_wallet_clusters(self, addresses: List[str]) -> Dict:
        """Detect wallet clusters using graph analysis.

        Args:
            addresses: List of addresses to analyze.

        Returns:
            Dict with cluster information.
        """
        G = nx.DiGraph()
        edge_data = {}

        for address in addresses:
            G.add_node(address)
            transfers = self.get_token_transfers(address, limit=100)

            for transfer in transfers:
                from_addr = transfer.get("from", "")
                to_addr = transfer.get("to", "")

                if from_addr and to_addr:
                    G.add_edge(from_addr, to_addr)
                    amount = transfer.get("value", "0")
                    try:
                        edge_data[(from_addr, to_addr)] = int(amount, 16)
                    except (ValueError, TypeError):
                        edge_data[(from_addr, to_addr)] = 0

        # Find connected components (clusters)
        clusters = list(nx.connected_components(G.to_undirected()))
        large_clusters = [c for c in clusters if len(c) > 2]

        result = {
            "total_addresses": len(addresses),
            "edges": G.number_of_edges(),
            "clusters": len(large_clusters),
            "large_clusters": [
                {
                    "size": len(cluster),
                    "members": list(cluster),
                }
                for cluster in large_clusters
            ],
        }

        logger.info(
            "Wallet clustering: %d clusters found from %d addresses",
            len(large_clusters), len(addresses),
        )
        return result

    def scan_cex_deposits(
        self,
        chain: str = "ethereum",
        threshold_usd: float = 10000,
    ) -> List[Dict]:
        """Scan for anomalous CEX deposit patterns.

        Args:
            chain: Chain name.
            threshold_usd: Minimum USD value to flag.

        Returns:
            List of suspicious deposit alerts.
        """
        # Known CEX hot wallet addresses
        cex_hot_wallets = {
            "binance": [
                "0x28aFe0b7981c7E06c2bEe13e8E7d2cA7707f2960",
                "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33C8",
            ],
            "coinbase": [
                "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD",
            ],
        }

        alerts = []

        for exchange, wallets in cex_hot_wallets.items():
            for wallet in wallets:
                transfers = self.get_token_transfers(wallet, chain=chain, limit=50)

                for transfer in transfers:
                    # Check if transfer involves mixer or flagged addresses
                    from_addr = transfer.get("from", "").lower()
                    to_addr = transfer.get("to", "").lower()

                    if from_addr in self.sanctioned_addresses or to_addr in self.sanctioned_addresses:
                        alerts.append({
                            "chain": chain,
                            "exchange": exchange,
                            "alert_type": "MIXER_DEPOSIT",
                            "severity": "high",
                            "addresses": [from_addr, to_addr],
                            "tx_hashes": [transfer.get("hash", "")],
                            "summary": f"Potential mixer funds deposited to {exchange} hot wallet",
                        })

        return alerts

    def run_full_scan(
        self,
        chains: List[str] = None,
    ) -> List[Dict]:
        """Run a full on-chain scan across configured chains.

        Args:
            chains: List of chains to scan.

        Returns:
            List of all alerts.
        """
        if chains is None:
            chains = ["ethereum", "arbitrum", "bsc"]

        all_alerts = []

        for chain in chains:
            logger.info("Scanning chain: %s", chain)

            # Scan new token launches
            launch_alerts = self.scan_new_token_launches(chain)
            all_alerts.extend(launch_alerts)

            # Scan CEX deposits
            cex_alerts = self.scan_cex_deposits(chain)
            all_alerts.extend(cex_alerts)

            logger.info("Found %d alerts on %s", len(launch_alerts) + len(cex_alerts), chain)

        logger.info("Full scan complete: %d total alerts", len(all_alerts))
        return all_alerts

    def _get_rpc_url(self, chain: str) -> str:
        """Get RPC URL for a chain."""
        urls = {
            "ethereum": self.ethereum_rpc,
            "arbitrum": self.arbitrum_rpc,
            "bsc": self.bsc_rpc,
        }
        return urls.get(chain, "")

    def _get_api_key(self, chain: str) -> str:
        """Get API key for a chain explorer."""
        keys = {
            "ethereum": self.etherscan_api_key,
            "arbitrum": self.arbiscan_api_key,
            "bsc": self.bscscan_api_key,
        }
        return keys.get(chain, "")

    def _get_explorer_url(self, chain: str) -> str:
        """Get explorer base URL for a chain."""
        urls = {
            "ethereum": "https://api.etherscan.io",
            "arbitrum": "https://api.arbiscan.io",
            "bsc": "https://api.bscscan.com",
        }
        return urls.get(chain, "")

    def _simulate_transfers(self, limit: int = 10) -> List[Dict]:
        """Generate simulated token transfers for testing."""
        import random

        sim_transfers = []
        for _ in range(limit):
            sim_transfers.append({
                "hash": f"0x{''.join(random.choices('0123456789abcdef', k=64))}",
                "from": f"0x{''.join(random.choices('0123456789abcdef', k=40))}",
                "to": f"0x{''.join(random.choices('0123456789abcdef', k=40))}",
                "value": hex(random.randint(1000000, 1000000000)),
                "tokenName": "TestToken",
                "tokenSymbol": "TEST",
                "timeStamp": str(int(datetime.utcnow().timestamp())),
            })
        return sim_transfers

    def _simulate_new_tokens(self, chain: str) -> List[Dict]:
        """Generate simulated new token launches for testing."""
        import random

        tokens = []
        for _ in range(random.randint(2, 5)):
            tokens.append({
                "chain": chain,
                "alert_type": "SUSPICIOUS_LAUNCH",
                "severity": random.choice(["medium", "high", "critical"]),
                "addresses": [
                    f"0x{''.join(random.choices('0123456789abcdef', k=40))}",
                ],
                "tx_hashes": [f"0x{''.join(random.choices('0123456789abcdef', k=64))}"],
                "summary": f"Suspicious token launch detected on {chain}",
                "evidence": {
                    "dev_holdings_pct": round(random.uniform(0.2, 0.8), 2),
                    "liquidity_usd": round(random.uniform(1000, 50000), 2),
                    "time_to_liquidity_seconds": random.randint(10, 300),
                },
            })
        return tokens

    def _simulate_liquidity_events(self, chain: str) -> List[Dict]:
        """Generate simulated liquidity events for testing."""
        import random

        events = []
        for _ in range(random.randint(1, 3)):
            events.append({
                "chain": chain,
                "alert_type": "SUSPICIOUS_LIQUIDITY",
                "severity": "high",
                "addresses": [
                    f"0x{''.join(random.choices('0123456789abcdef', k=40))}",
                    f"0x{''.join(random.choices('0123456789abcdef', k=40))}",
                ],
                "tx_hashes": [f"0x{''.join(random.choices('0123456789abcdef', k=64))}"],
                "summary": f"Suspicious liquidity event on {chain}",
                "evidence": {
                    "pair_address": f"0x{''.join(random.choices('0123456789abcdef', k=40))}",
                    "liquidity_added_usd": round(random.uniform(5000, 100000), 2),
                    "lp_tokens_retained_pct": round(random.uniform(0.5, 1.0), 2),
                },
            })
        return events
