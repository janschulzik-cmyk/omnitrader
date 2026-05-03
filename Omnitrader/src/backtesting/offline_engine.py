"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/backtesting/offline_engine.py.enc \
        --key 85b4db1409b2e5a6f81e8e6565a816716a5d85c083c29fe3838a5c3c0fa8e38f
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/backtesting/offline_engine.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/backtesting/offline_engine.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/backtesting/offline_engine.py.enc --key 85b4db1409b2e5a6f81e8e6565a816716a5d85c083c29fe3838a5c3c0fa8e38f to decrypt."
)
