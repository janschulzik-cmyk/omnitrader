"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/privacy/opt_out_automator.py.enc \
        --key 7c7fc3b3c7ea751f206b42a8fc12db550a572943838c0bf8872394d6f560c653
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/privacy/opt_out_automator.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/privacy/opt_out_automator.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/privacy/opt_out_automator.py.enc --key 7c7fc3b3c7ea751f206b42a8fc12db550a572943838c0bf8872394d6f560c653 to decrypt."
)
