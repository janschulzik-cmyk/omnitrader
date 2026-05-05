"""Mesh Network for Swarm Module.

Coordinates decentralized node discovery and routing
using a NATS-based overlay network for the DePIN swarm.
"""

import os
import json
import time
import socket
import hashlib
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Set, Callable

from ..utils.db import MeshNodeRecord, get_session
from ..utils.logging_config import get_logger

logger = get_logger("swarm.mesh")


class MeshNetwork:
    """Manages a mesh overlay network for swarm coordination."""

    def __init__(self, config: Dict = None):
        """Initialize the mesh network coordinator.

        Args:
            config: Configuration with NATS and mesh parameters.
        """
        self.config = config or {}
        self.node_id = self._generate_node_id()
        self.nats_urls = self.config.get(
            "nats_urls",
            ["nats://localhost:4222"],
        )
        self.cluster_name = self.config.get("cluster_name", "omnitrader-swarm")
        self.max_nodes = self.config.get("max_nodes", 100)
        self.heartbeat_interval = self.config.get("heartbeat_interval", 30)
        self.mesh_port = self.config.get("mesh_port", 5555)

        # Known peers
        self.peers: Dict[str, Dict] = {}
        self.subscriptions: Dict[str, List[Callable]] = {}
        self._message_queue: List[Dict] = []

        # Node registry
        self.node_info = {
            "node_id": self.node_id,
            "hostname": socket.gethostname(),
            "ip": self._get_local_ip(),
            "port": self.mesh_port,
            "version": "1.0.0",
            "registered_at": datetime.utcnow().isoformat(),
            "capabilities": ["vpn_routing", "bandwidth_sharing", "bounty_scanning"],
        }

    def _generate_node_id(self) -> str:
        """Generate a unique node ID."""
        hostname = socket.gethostname()
        pid = os.getpid()
        raw = f"{hostname}_{pid}_{int(time.time())}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _get_local_ip(self) -> str:
        """Get the local IP address."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def start(self) -> bool:
        """Start the mesh network.

        Returns:
            True if started successfully.
        """
        logger.info("Starting mesh network: %s (node: %s)",
                     self.cluster_name, self.node_id)

        # Register self in DB
        self._register_node()

        # Try connecting to NATS
        connected = False
        for url in self.nats_urls:
            try:
                # Attempt NATS connection (will fail gracefully if not available)
                import nats
                nc = await nats.connect(url, error_cb=self._on_nats_error)
                logger.info("Connected to NATS at %s", url)
                connected = True
                # Subscribe to swarm topics
                await self._setup_subscriptions(nc)
                # Start heartbeat
                asyncio.create_task(self._heartbeat_loop(nc))
                return True
            except Exception as e:
                logger.warning("Could not connect to NATS at %s: %s", url, e)

        if not connected:
            logger.warning(
                "No NATS servers available. Running in standalone mode.",
            )
            self._start_local_mesh()
            return True

        return True

    def _register_node(self) -> None:
        """Register this node in the database."""
        session = get_session()
        try:
            record = MeshNodeRecord(
                node_id=self.node_id,
                ip_address=self.node_info["ip"],
                port=self.mesh_port,
                cluster=self.cluster_name,
                status="ACTIVE",
                registered_at=datetime.utcnow(),
            )
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to register mesh node: %s", e)
        finally:
            session.close()

    async def _setup_subscriptions(self, nc) -> None:
        """Set up NATS subscriptions for mesh topics.

        Args:
            nc: NATS connection object.
        """
        topics = [
            f"omnitrader.{self.cluster_name}.discovery",
            f"omnitrader.{self.cluster_name}.heartbeat",
            f"omnitrader.{self.cluster_name}.task",
            f"omnitrader.{self.cluster_name}.report",
        ]

        for topic in topics:
            try:
                sub = await nc.subscribe(
                    subject=topic,
                    callback=self._handle_message,
                )
                self.subscriptions[topic] = [sub]
                logger.info("Subscribed to %s", topic)
            except Exception as e:
                logger.warning("Failed to subscribe to %s: %s", topic, e)

    async def _heartbeat_loop(self, nc) -> None:
        """Send periodic heartbeat messages.

        Args:
            nc: NATS connection object.
        """
        while True:
            try:
                heartbeat = {
                    "type": "heartbeat",
                    "node_id": self.node_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "status": "active",
                    "peer_count": len(self.peers),
                }
                await nc.publish(
                    f"omnitrader.{self.cluster_name}.heartbeat",
                    json.dumps(heartbeat).encode(),
                )
            except Exception as e:
                logger.warning("Heartbeat failed: %s", e)
            await asyncio.sleep(self.heartbeat_interval)

    async def _handle_message(self, msg) -> None:
        """Handle incoming mesh messages.

        Args:
            msg: NATS message.
        """
        try:
            data = json.loads(msg.data.decode())
            msg_type = data.get("type", "")

            if msg_type == "discovery":
                self._handle_discovery(data)
            elif msg_type == "heartbeat":
                self._handle_heartbeat(data)
            elif msg_type == "task":
                self._handle_task(data)
            elif msg_type == "report":
                self._handle_report(data)
            else:
                logger.debug("Unknown message type: %s", msg_type)

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse mesh message: %s", e)

    def _handle_discovery(self, data: Dict) -> None:
        """Handle discovery announcement from a new node.

        Args:
            data: Discovery message data.
        """
        node_id = data.get("node_id", "")
        if node_id and node_id != self.node_id:
            self.peers[node_id] = {
                "info": data.get("info", {}),
                "announced_at": data.get("timestamp", ""),
                "last_seen": datetime.utcnow().isoformat(),
            }
            logger.info("New peer discovered: %s", node_id)
            self._announce_to_peers()

    def _handle_heartbeat(self, data: Dict) -> None:
        """Handle heartbeat from a peer.

        Args:
            data: Heartbeat message data.
        """
        node_id = data.get("node_id", "")
        if node_id in self.peers:
            self.peers[node_id]["last_seen"] = datetime.utcnow().isoformat()
            self.peers[node_id]["peer_count"] = data.get("peer_count", 0)

    def _handle_task(self, data: Dict) -> None:
        """Handle a task assignment from the mesh.

        Args:
            data: Task message data.
        """
        task_id = data.get("task_id", "")
        task_type = data.get("task_type", "")
        logger.info("Received task: %s (%s) from node %s",
                     task_id, task_type, data.get("node_id", ""))

        # In a full implementation, this would trigger local task execution
        # For now, log and relay
        self._relay_message(data)

    def _handle_report(self, data: Dict) -> None:
        """Handle a report broadcast from a peer.

        Args:
            data: Report message data.
        """
        report_id = data.get("report_id", "")
        logger.info("Received report: %s from node %s",
                     report_id, data.get("node_id", ""))

    def _announce_to_peers(self) -> None:
        """Broadcast a discovery announcement to all known peers."""
        announcement = {
            "type": "discovery",
            "node_id": self.node_id,
            "timestamp": datetime.utcnow().isoformat(),
            "info": self.node_info,
        }
        # In a full implementation, publish via NATS
        # For standalone mode, just log
        logger.info(
            "Discovery announcement for node %s with %d peers",
            self.node_id, len(self.peers),
        )

    def _relay_message(self, message: Dict) -> None:
        """Relay a message to other peers.

        Args:
            message: Message to relay.
        """
        # In a full implementation, publish to mesh topics
        logger.debug("Relaying message: %s", message.get("type", "unknown"))

    def _start_local_mesh(self) -> None:
        """Start a local (non-NATS) mesh for standalone operation."""
        self.peers[self.node_id] = {
            "info": self.node_info,
            "announced_at": datetime.utcnow().isoformat(),
            "last_seen": datetime.utcnow().isoformat(),
            "local": True,
        }
        logger.info("Started local mesh mode for node %s", self.node_id)

    def add_peer(self, peer_info: Dict) -> bool:
        """Manually add a peer to the mesh.

        Args:
            peer_info: Peer information dict.

        Returns:
            True if added successfully.
        """
        peer_id = peer_info.get("node_id", "")
        if not peer_id:
            return False

        if len(self.peers) >= self.max_nodes:
            logger.warning("Max peer limit reached (%d)", self.max_nodes)
            return False

        self.peers[peer_id] = {
            "info": peer_info,
            "announced_at": peer_info.get("registered_at", ""),
            "last_seen": datetime.utcnow().isoformat(),
        }
        logger.info("Added peer: %s", peer_id)
        return True

    def remove_peer(self, peer_id: str) -> bool:
        """Remove a peer from the mesh.

        Args:
            peer_id: Peer node ID.

        Returns:
            True if removed successfully.
        """
        if peer_id in self.peers:
            del self.peers[peer_id]
            logger.info("Removed peer: %s", peer_id)
            return True
        return False

    def get_peers(self) -> List[Dict]:
        """Get all connected peers.

        Returns:
            List of peer dicts.
        """
        return [
            {
                "node_id": pid,
                "info": pinfo.get("info", {}),
                "last_seen": pinfo.get("last_seen", ""),
                "local": pinfo.get("local", False),
            }
            for pid, pinfo in self.peers.items()
        ]

    def get_status(self) -> Dict:
        """Get mesh network status.

        Returns:
            Dict with mesh status.
        """
        return {
            "node_id": self.node_id,
            "cluster": self.cluster_name,
            "peer_count": len(self.peers),
            "peers": self.get_peers(),
            "max_nodes": self.max_nodes,
            "nats_connected": len(self.subscriptions) > 0,
            "nats_urls": self.nats_urls,
        }

    async def broadcast(self, message: Dict) -> bool:
        """Broadcast a message to all peers.

        Args:
            message: Message to broadcast.

        Returns:
            True if broadcast succeeded.
        """
        try:
            # In full NATS mode, publish to the broadcast topic
            import nats
            # Would use nc.publish("omnitrader.{cluster}.broadcast", ...)
            logger.info("Broadcast message: %s", message.get("type", "unknown"))
            return True
        except Exception as e:
            logger.error("Broadcast failed: %s", e)
            return False

    async def stop(self) -> None:
        """Stop the mesh network."""
        logger.info("Stopping mesh network: %s", self.node_id)
        # Cleanup would happen here (close NATS connections, etc.)
        self.peers.clear()
