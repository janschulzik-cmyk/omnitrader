"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/sentinel/sanction_reporter.py.enc \
        --key f94359c06fec5c6f4eded71cfc71792eaa15bc45a5c68715243d3a31f9407bcd
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/sentinel/sanction_reporter.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/sentinel/sanction_reporter.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/sentinel/sanction_reporter.py.enc --key f94359c06fec5c6f4eded71cfc71792eaa15bc45a5c68715243d3a31f9407bcd to decrypt."
)
