"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \
        --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/decision_fusion.py.enc \
        --key 377fa1daffe845a41dfe12ea09c042f6a0f4697eac4738fd5ac5e85fa5b702b9
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/decision_fusion.py.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "/home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/decision_fusion.py"
raise ImportError(
    f"CoreGuard encrypted module: {_enc_file}. "
    "Use ouroboros_loader.py --encrypted /home/joe/ouroboros/cathedral/Omnitrader/src/analyst_bureau/decision_fusion.py.enc --key 377fa1daffe845a41dfe12ea09c042f6a0f4697eac4738fd5ac5e85fa5b702b9 to decrypt."
)
