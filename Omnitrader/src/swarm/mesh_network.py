"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/swarm/mesh_network.py.enc \
        --key e6a12f275f6e93a08d15ff7851bdaeb2e3ddb7c3cd8795e6b0c53de09c20cb88
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/swarm/mesh_network.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/swarm/mesh_network.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/swarm/mesh_network.py.enc --key e6a12f275f6e93a08d15ff7851bdaeb2e3ddb7c3cd8795e6b0c53de09c20cb88 to decrypt."
)
