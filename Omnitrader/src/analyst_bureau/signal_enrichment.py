"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/signal_enrichment.py.enc \
        --key ed0385312ecd9b00d2afcb2a07e31a11524cd6fb00cdc521dab09c7d35a6a4c4
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/signal_enrichment.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/signal_enrichment.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/signal_enrichment.py.enc --key ed0385312ecd9b00d2afcb2a07e31a11524cd6fb00cdc521dab09c7d35a6a4c4 to decrypt."
)
