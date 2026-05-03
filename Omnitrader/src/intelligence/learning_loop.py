"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/intelligence/learning_loop.py.enc \
        --key 68f616d952f1a7867f87a5f8454e0a456859c1cf85770e28d72259de924ed987
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/intelligence/learning_loop.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/intelligence/learning_loop.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/intelligence/learning_loop.py.enc --key 68f616d952f1a7867f87a5f8454e0a456859c1cf85770e28d72259de924ed987 to decrypt."
)
