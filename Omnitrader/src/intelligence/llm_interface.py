"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/intelligence/llm_interface.py.enc \
        --key 02407066088a7124f768b077a3dd4e6c77665a5ef5d97b8bf82c033cb67070a7
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/intelligence/llm_interface.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/intelligence/llm_interface.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/intelligence/llm_interface.py.enc --key 02407066088a7124f768b077a3dd4e6c77665a5ef5d97b8bf82c033cb67070a7 to decrypt."
)
