"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/legal/filing_dispatcher.py.enc \
        --key ae6e59dd5c9c663ea8c9212cc61f9678e250ff4c1c4785f9315b9d630e394b91
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/legal/filing_dispatcher.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/legal/filing_dispatcher.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/legal/filing_dispatcher.py.enc --key ae6e59dd5c9c663ea8c9212cc61f9678e250ff4c1c4785f9315b9d630e394b91 to decrypt."
)
