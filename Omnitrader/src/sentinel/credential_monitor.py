"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/sentinel/credential_monitor.py.enc \
        --key 44a67e23d73d8235ef3e72b096245f25df3b86c9b89d8dcb6e744fe65ffdf59f
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/sentinel/credential_monitor.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/sentinel/credential_monitor.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/sentinel/credential_monitor.py.enc --key 44a67e23d73d8235ef3e72b096245f25df3b86c9b89d8dcb6e744fe65ffdf59f to decrypt."
)
