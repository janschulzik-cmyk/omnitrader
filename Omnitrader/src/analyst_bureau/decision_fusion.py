"""Fuses Striker signal with Analyst Bureau output for final position sizing."""

import os
from typing import Dict, Optional

from ..utils.logging_config import get_logger

logger = get_logger("analyst_bureau.fusion")


def fuse_signal(original_signal: Dict, enriched_signal: Dict, hydra_striker_balance: float) -> Optional[Dict]:
    """Combine the original and enriched signals to produce the final trade decision.

    Returns:
        Final trade signal dict, or None if the trade should be cancelled.
    """
    # If Bureau was enabled but not approved, block the trade
    if os.environ.get("ANALYST_BUREAU_ENABLED", "false").lower() == "true":
        if not enriched_signal.get("bureau_approved", True):
            logger.info("Trade rejected by Analyst Bureau: %s", enriched_signal.get("debate_summary", ""))
            return None

    # Start with enriched signal as base
    final = dict(enriched_signal)

    # Adjust position size using risk factors
    base_risk_pct = 0.02  # default 2%
    risk_multiplier = enriched_signal.get("risk_adjustment", 1.0) * enriched_signal.get("confidence_modifier", 1.0)
    adjusted_risk = base_risk_pct * risk_multiplier

    # Clamp risk to safe bounds (0.5% to 5%)
    adjusted_risk = max(0.005, min(0.05, adjusted_risk))

    # Calculate position size from adjusted risk and stop distance
    entry = final.get("entry_price", 0)
    stop = final.get("stop_loss", 0)
    if stop == 0 or entry == 0:
        logger.warning("Missing entry/stop prices; using default risk.")
        position_size = (hydra_striker_balance * adjusted_risk) / 1.0  # fallback
    else:
        risk_per_share = abs(entry - stop)
        risk_amount = hydra_striker_balance * adjusted_risk
        position_size = risk_amount / risk_per_share if risk_per_share > 0 else 0.0

    final["position_size"] = round(position_size, 6)
    final["risk_per_trade_pct"] = adjusted_risk
    final["analyst_bureau_used"] = True

    logger.info("Fused signal: pair=%s, pos_size=%.6f, risk=%.2f%%",
                final.get("pair"), position_size, adjusted_risk * 100)
    return final
