"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/bureau_orchestrator.py.enc \
        --key bebaa5e745c25b90fecd27fe1b6656825bb9b19e04ca71f231f0500580820d81
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/bureau_orchestrator.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/bureau_orchestrator.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/bureau_orchestrator.py.enc --key bebaa5e745c25b90fecd27fe1b6656825bb9b19e04ca71f231f0500580820d81 to decrypt."
)
