"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/striker/news_monitor.py.enc \
        --key ac76e2f19df61d105b22e5b0c47b0749feb3fe2079b2b1a11a3d21ebb6667b71
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/striker/news_monitor.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/striker/news_monitor.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/striker/news_monitor.py.enc --key ac76e2f19df61d105b22e5b0c47b0749feb3fe2079b2b1a11a3d21ebb6667b71 to decrypt."
)
