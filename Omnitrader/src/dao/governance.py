"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/dao/governance.py.enc \
        --key 53471524c8acef9b9525a39bd67917d2fbe5dbece226c3491aa99692aff06f25
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/dao/governance.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/dao/governance.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/dao/governance.py.enc --key 53471524c8acef9b9525a39bd67917d2fbe5dbece226c3491aa99692aff06f25 to decrypt."
)
