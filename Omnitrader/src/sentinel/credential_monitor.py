"""Credential monitor for Omnitrader Sentinel.

Watches API and Telegram bot logs for repeated
failed authentication attempts. If a threshold is
exceeded, the offending IP is blocked and an alert
is generated.
"""

import os
import json
import time
import socket
import asyncio
import threading
import ipaddress
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set
from collections import defaultdict

from ..utils.logging_config import get_logger
from ..utils.db import OnChainAlert, get_session

logger = get_logger("sentinel.credential")


class CredentialMonitorConfig:
    """Configuration for the credential monitor."""
    max_failed_attempts: int = 10
    block_duration_seconds: int = 3600  # 1 hour
    check_interval: int = 60  # seconds
    blocklist_path: str = "/etc/omnitrader/ip_blocklist.json"
    allowed_networks: List[str] = None  # Override to whitelist certain networks


class CredentialMonitor:
    """Monitors authentication failures and blocks brute-force attackers."""

    _instance: Optional["CredentialMonitor"] = None
    _lock = threading.Lock()

    def __init__(self, config: CredentialMonitorConfig = None):
        self.config = config or CredentialMonitorConfig()
        self.failed_attempts: Dict[str, List[float]] = defaultdict(list)
        self.blocked_ips: Set[str] = set()
        self._load_blocklist()
        self._allowed_networks = [
            ipaddress.ip_network(n)
            for n in (self.config.allowed_networks or ["127.0.0.0/8"])
        ]

    @classmethod
    def load(cls) -> "CredentialMonitor":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = CredentialMonitor()
        return cls._instance

    def _load_blocklist(self) -> None:
        """Load previously blocked IPs from disk."""
        try:
            with open(self.config.blocklist_path) as f:
                data = json.load(f)
                self.blocked_ips = set(data.get("blocked_ips", []))
        except (FileNotFoundError, json.JSONDecodeError):
            self.blocked_ips = set()

    def _save_blocklist(self) -> None:
        """Save blocked IPs to disk."""
        os.makedirs(os.path.dirname(self.config.blocklist_path), exist_ok=True)
        with open(self.config.blocklist_path, "w") as f:
            json.dump({"blocked_ips": list(self.blocked_ips)}, f, indent=2)

    def record_failure(self, ip: str) -> bool:
        """Record a failed authentication attempt.

        Args:
            ip: The IP address that failed.

        Returns:
            True if the IP has been blocked.
        """
        # Skip whitelisted networks
        try:
            addr = ipaddress.ip_address(ip)
            for net in self._allowed_networks:
                if addr in net:
                    return False
        except ValueError:
            return False  # Invalid IP

        # Don't block already blocked IPs
        if ip in self.blocked_ips:
            return True

        now = time.time()
        self.failed_attempts[ip].append(now)

        # Remove attempts older than 15 minutes
        cutoff = now - 900
        self.failed_attempts[ip] = [
            t for t in self.failed_attempts[ip] if t > cutoff
        ]

        count = len(self.failed_attempts[ip])

        if count >= self.config.max_failed_attempts:
            self._block_ip(ip, count)
            return True

        return False

    def _block_ip(self, ip: str, attempts: int) -> None:
        """Block an IP address.

        Args:
            ip: IP to block.
            attempts: Number of failed attempts.
        """
        if ip in self.blocked_ips:
            return

        self.blocked_ips.add(ip)
        self._save_blocklist()

        logger.warning(
            "IP BLOCKED: %s (%d failed attempts)",
            ip, attempts,
        )

        # Log to DB
        session = get_session()
        try:
            alert = OnChainAlert(
                alert_type="credential_brute_force",
                network="local",
                severity="HIGH",
                target_address=ip,
                evidence=json.dumps({
                    "attempt_count": attempts,
                    "block_duration": self.config.block_duration_seconds,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }),
                value_usd=None,
                submitted_as_bounty=False,
            )
            session.add(alert)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to log credential alert: %s", e)
        finally:
            session.close()

    async def start_monitoring(self) -> None:
        """Start periodic monitoring."""
        while True:
            try:
                blocked = self.check_and_block()
                if blocked:
                    logger.warning(
                        "credential_monitor: blocked %d IPs",
                        len(blocked),
                    )
            except Exception as e:
                logger.error("credential_monitor error: %s", e)
            await asyncio.sleep(self.config.check_interval)

    def check_and_block(self) -> List[str]:
        """Check for expired blocks and clean up stale data.

        Returns:
            List of IPs that were newly blocked in this check.
        """
        now = time.time()
        newly_blocked = []

        # Clean up expired blocks
        expired = []
        for ip in list(self.blocked_ips):
            # In a full implementation, track block expiry time
            # For now, blocks are permanent (or until manual unblock)
            pass

        # Clean up old failed attempts
        for ip in list(self.failed_attempts.keys()):
            cutoff = now - 900
            self.failed_attempts[ip] = [
                t for t in self.failed_attempts[ip] if t > cutoff
            ]
            if not self.failed_attempts[ip]:
                del self.failed_attempts[ip]

        return newly_blocked

    def is_blocked(self, ip: str) -> bool:
        """Check if an IP is blocked.

        Args:
            ip: IP address.

        Returns:
            True if blocked.
        """
        return ip in self.blocked_ips

    def get_stats(self) -> Dict:
        """Get credential monitoring statistics.

        Returns:
            Dict with stats.
        """
        return {
            "total_blocked_ips": len(self.blocked_ips),
            "active_failed_attempts": {
                ip: len(timestamps)
                for ip, timestamps in self.failed_attempts.items()
                if timestamps
            },
            "blocked_count": len(self.blocked_ips),
        }

    def unblock_ip(self, ip: str) -> bool:
        """Manually unblock an IP.

        Args:
            ip: IP to unblock.

        Returns:
            True if the IP was unblocked.
        """
        if ip in self.blocked_ips:
            self.blocked_ips.discard(ip)
            self._save_blocklist()
            logger.info("IP unblocked: %s", ip)
            return True
        return False
