"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/apis/routes.py.enc \
        --key 26135be94e9c94c2bf93be0624f3d74e85425c19f431d30f23f7e70f05af24fc
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/apis/routes.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/apis/routes.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/apis/routes.py.enc --key 26135be94e9c94c2bf93be0624f3d74e85425c19f431d30f23f7e70f05af24fc to decrypt."
)
