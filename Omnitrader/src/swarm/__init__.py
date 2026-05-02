"""Swarm Module: DePIN VPN, token rewards, and mesh networking.

Coordinates decentralized node operators, manages bandwidth
sharing, and handles WATCHDOG token distribution.
"""

from .vpn_node import VPNNode
from .mesh_network import MeshNetwork

# TokenRewards requires web3 — load lazily if available
try:
    from .token_rewards import TokenRewards
    _HAS_WEB3 = True
except ImportError:
    TokenRewards = None  # type: ignore
    _HAS_WEB3 = False

__all__ = ["VPNNode", "MeshNetwork"]
if _HAS_WEB3:
    __all__.append("TokenRewards")
