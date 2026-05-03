"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/utils/db.py.enc \
        --key db6e71ca46e1ddee69e21675e0e977674728b7db0dcd62d00734f7a966c2d1e6
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/utils/db.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/utils/db.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/utils/db.py.enc --key db6e71ca46e1ddee69e21675e0e977674728b7db0dcd62d00734f7a966c2d1e6 to decrypt."
)
