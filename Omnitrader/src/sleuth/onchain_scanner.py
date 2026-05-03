"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/sleuth/onchain_scanner.py.enc \
        --key cb3bfc73481fe76d6f3a6d694f6f37fc2e787c060a6a5fc790121692874f50d8
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/sleuth/onchain_scanner.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/sleuth/onchain_scanner.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/sleuth/onchain_scanner.py.enc --key cb3bfc73481fe76d6f3a6d694f6f37fc2e787c060a6a5fc790121692874f50d8 to decrypt."
)
