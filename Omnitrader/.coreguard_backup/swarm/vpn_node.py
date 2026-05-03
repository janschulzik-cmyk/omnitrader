"""VPN Node Manager for Swarm Module.

Manages WireGuard VPN node operations, bandwidth routing,
and endpoint rotation for the DePIN swarm.
"""

import os
import time
import socket
import hashlib
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import yaml

from ..utils.db import VPNNodeRecord, get_session
from ..utils.logging_config import get_logger

logger = get_logger("swarm.vpn")


class VPNNode:
    """Manages a WireGuard VPN node for the DePIN swarm."""

    def __init__(self, config: Dict = None):
        """Initialize the VPN node manager.

        Args:
            config: Configuration with WireGuard settings and keys.
        """
        self.config = config or {}
        self.node_id = self.config.get("node_id", self._generate_node_id())
        self.private_key = os.environ.get("VPN_PRIVATE_KEY", "")
        self.public_key = os.environ.get("VPN_PUBLIC_KEY", "")
        self.listen_port = self.config.get("listen_port", 51820)
        self.allowed_ips = self.config.get("allowed_ips", ["0.0.0.0/0"])
        self.dns = self.config.get("dns", "1.1.1.1")
        self.mtu = self.config.get("mtu", 1420)

        # Peer configuration (other swarm nodes)
        self.peers: List[Dict] = []

        # Routing configuration
        self.routing_table: Dict[str, str] = {}  # destination -> peer_pubkey
        self.bandwidth_limit_mbps = self.config.get("bandwidth_limit_mbps", 100)

        # Metrics
        self.bytes_sent = 0
        self.bytes_received = 0
        self.start_time = datetime.utcnow()

    def _generate_node_id(self) -> str:
        """Generate a unique node ID from hostname and timestamp."""
        hostname = socket.gethostname()
        timestamp = str(int(datetime.utcnow().timestamp()))
        raw = f"{hostname}_{timestamp}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def configure_wireguard(self, peers: List[Dict]) -> bool:
        """Configure WireGuard interface with peers.

        Args:
            peers: List of peer dicts with public_key, endpoint, allowed_ips.

        Returns:
            True if configuration was successful.
        """
        if not self.private_key:
            logger.error("VPN private key not configured.")
            return False

        self.peers = peers

        # Generate WireGuard config
        config_content = self._generate_wireguard_config(peers)

        # Write config to temporary file
        config_path = f"/tmp/wg-swarm-{self.node_id}.conf"
        try:
            with open(config_path, "w") as f:
                f.write(config_content)

            # Reload WireGuard interface
            result = subprocess.run(
                ["wg-quick", "down", f"swarm-{self.node_id}"],
                capture_output=True,
                text=True,
            )

            result = subprocess.run(
                ["wg-quick", "up", config_path],
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                logger.info(
                    "WireGuard configured with %d peers (node: %s)",
                    len(peers), self.node_id,
                )
                self._update_peer_routing(peers)
                self._register_node(peers)
                return True
            else:
                logger.error(
                    "WireGuard config failed: %s", result.stderr,
                )
                return False

        except FileNotFoundError:
            logger.warning("wg-quick not found. Running in simulation mode.")
            self._simulate_node_registration(peers)
            return True
        except Exception as e:
            logger.error("Failed to configure WireGuard: %s", e)
            return False

    def _generate_wireguard_config(self, peers: List[Dict]) -> str:
        """Generate WireGuard configuration content.

        Args:
            peers: List of peer dicts.

        Returns:
            WireGuard config string.
        """
        config = f"""
[Interface]
PrivateKey = {self.private_key}
Address = 10.133.0.1/24
ListenPort = {self.listen_port}
DNS = {self.dns}
MTU = {self.mtu}
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE
"""

        for peer in peers:
            peer_config = f"""
[Peer]
PublicKey = {peer.get('public_key', '')}
Endpoint = {peer.get('endpoint', 'localhost:51820')}
AllowedIPs = {peer.get('allowed_ips', '10.133.0.0/24')}
PersistentKeepalive = 25
"""
            config += peer_config

        return config

    def _update_peer_routing(self, peers: List[Dict]) -> None:
        """Update the routing table for peer communication.

        Args:
            peers: List of peer dicts.
        """
        for i, peer in enumerate(peers):
            ip = f"10.133.0.{i + 2}"
            self.routing_table[ip] = peer.get("public_key", "")

        logger.info("Updated routing table: %d entries", len(self.routing_table))

    def _register_node(self, peers: List[Dict]) -> None:
        """Register this node in the database.

        Args:
            peers: List of known peers.
        """
        session = get_session()
        try:
            record = VPNNodeRecord(
                node_id=self.node_id,
                public_key=self.public_key,
                listen_port=self.listen_port,
                peer_count=len(peers),
                status="ACTIVE",
                registered_at=datetime.utcnow(),
            )
            session.add(record)
            session.commit()
            logger.info("Node %s registered with %d peers", self.node_id, len(peers))
        except Exception as e:
            session.rollback()
            logger.error("Failed to register node: %s", e)
        finally:
            session.close()

    def _simulate_node_registration(self, peers: List[Dict]) -> None:
        """Simulate node registration (for when WireGuard is not available).

        Args:
            peers: List of known peers.
        """
        session = get_session()
        try:
            record = VPNNodeRecord(
                node_id=self.node_id,
                public_key=self.public_key or "simulated",
                listen_port=self.listen_port,
                peer_count=len(peers),
                status="SIMULATED",
                registered_at=datetime.utcnow(),
            )
            session.add(record)
            session.commit()
            logger.info(
                "Node %s SIMULATED registration with %d peers",
                self.node_id, len(peers),
            )
        except Exception as e:
            session.rollback()
            logger.error("Failed to register node: %s", e)
        finally:
            session.close()

    def get_node_status(self) -> Dict:
        """Get current node status and metrics.

        Returns:
            Dict with node status information.
        """
        uptime_seconds = (datetime.utcnow() - self.start_time).total_seconds()

        # Try to get WireGuard stats
        wg_stats = {}
        try:
            result = subprocess.run(
                ["wg", "show", f"swarm-{self.node_id}", "transfer"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) >= 2:
                    parts = lines[1].split()
                    if len(parts) >= 2:
                        wg_stats["bytes_sent"] = int(parts[0])
                        wg_stats["bytes_received"] = int(parts[1])
                        self.bytes_sent = wg_stats["bytes_sent"]
                        self.bytes_received = wg_stats["bytes_received"]
        except FileNotFoundError:
            pass

        return {
            "node_id": self.node_id,
            "status": "ACTIVE" if self.public_key else "INACTIVE",
            "uptime_seconds": uptime_seconds,
            "uptime_formatted": self._format_uptime(uptime_seconds),
            "peer_count": len(self.peers),
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "routing_table_size": len(self.routing_table),
            "bandwidth_limit_mbps": self.bandwidth_limit_mbps,
        }

    def _format_uptime(self, seconds: float) -> str:
        """Format uptime seconds into a human-readable string.

        Args:
            seconds: Uptime in seconds.

        Returns:
            Formatted uptime string.
        """
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)

        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")

        return " ".join(parts)

    def rotate_endpoint(self, new_endpoint: str) -> bool:
        """Rotate to a new WireGuard endpoint.

        Args:
            new_endpoint: New endpoint address (host:port).

        Returns:
            True if rotation successful.
        """
        if not self.peers:
            logger.warning("No peers configured for endpoint rotation.")
            return False

        # Update first peer endpoint
        self.peers[0]["endpoint"] = new_endpoint
        logger.info("Rotated endpoint to: %s", new_endpoint)
        return True

    def get_config_dict(self) -> Dict:
        """Get the node configuration as a serializable dict.

        Returns:
            Configuration dict.
        """
        return {
            "node_id": self.node_id,
            "listen_port": self.listen_port,
            "allowed_ips": self.allowed_ips,
            "dns": self.dns,
            "mtu": self.mtu,
            "bandwidth_limit_mbps": self.bandwidth_limit_mbps,
            "peer_count": len(self.peers),
        }
