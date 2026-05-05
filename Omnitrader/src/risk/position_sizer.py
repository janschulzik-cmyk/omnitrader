"""Dynamic position sizing using fractional Kelly Criterion.

Calculates optimal position sizes based on backtest-derived
win rate and win/loss ratio, with safety caps.

Formula: f = (p * r_win - (1-p) * r_loss) / (r_win * r_loss)
  p      = win probability
  r_win  = average win % (as a ratio, e.g. 2.0 for 2:1)
  r_loss = average loss % (as a ratio)

The result is fractional Kelly (default 25%) for conservative sizing.
"""

import os
import math
from typing import Dict, Optional, Tuple

from ..utils.logging_config import get_logger

logger = get_logger("risk.position_sizer")

# ── Default parameters (can be overridden via config or env) ────────────

_DEFAULT_KELLY_FRACTION = float(os.environ.get("KELLY_FRACTION", "0.25"))
_DEFAULT_MAX_POSITION_PCT = float(os.environ.get("MAX_POSITION_PCT", "0.10"))  # 10% of pool max
_DEFAULT_MIN_POSITION_USD = float(os.environ.get("MIN_POSITION_USD", "5.00"))


def fractional_kelly(
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    *,
    kelly_fraction: float = None,
) -> float:
    """Calculate the fractional Kelly position size fraction.

    Args:
        win_rate: Probability of a winning trade (0-1).
        avg_win_pct: Average win as a positive percentage (e.g., 2.0 for 2%).
        avg_loss_pct: Average loss as a positive percentage (e.g., 1.0 for 1%).
        kelly_fraction: Fraction of full Kelly to use (default 0.25 = quarter-Kelly).

    Returns:
        Optimal fraction of capital to risk (0.0-1.0).
        Returns 0.0 if Kelly edge is negative (strategy has negative expectancy).
    """
    if kelly_fraction is None:
        kelly_fraction = _DEFAULT_KELLY_FRACTION

    if avg_win_pct <= 0 or avg_loss_pct <= 0:
        logger.warning("Cannot calculate Kelly: avg_win_pct=%.4f, avg_loss_pct=%.4f",
                       avg_win_pct, avg_loss_pct)
        return 0.0

    # Win/loss ratios (as multipliers)
    r_win = avg_win_pct / avg_loss_pct  # e.g., 2.0:1
    r_loss = 1.0

    # Kelly formula: f = (p * b - q) / b
    # where b = avg_win / avg_loss, q = 1 - p
    p = win_rate
    q = 1.0 - p
    b = r_win

    kelly = (p * b - q) / b

    if kelly <= 0:
        logger.info(
            "Kelly edge negative: win_rate=%.2f, avg_win=%.2f%%, avg_loss=%.2f%%, kelly=%.4f",
            win_rate, avg_win_pct, avg_loss_pct, kelly,
        )
        return 0.0

    fractional = kelly * kelly_fraction
    return min(fractional, _DEFAULT_MAX_POSITION_PCT)


# Alias for backward compatibility
fractional_kelly_size = fractional_kelly


def calc_position_size(
    pool_balance: float,
    entry_price: float,
    stop_loss: float,
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    *,
    kelly_fraction: float = None,
    max_position_fraction: float = None,
    min_position_usd: float = None,
    risk_per_trade_pct: float = None,
) -> Dict:
    """Calculate position size with Kelly Criterion and risk limits.

    Args:
        pool_balance: Available capital in the pool.
        entry_price: Trade entry price.
        stop_loss: Stop loss price.
        win_rate: Historical win rate (0-1).
        avg_win_pct: Average win percentage.
        avg_loss_pct: Average loss percentage.
        kelly_fraction: Fraction of Kelly to use.
        max_position_fraction: Maximum position as fraction of pool.
        min_position_usd: Minimum position value in USD.
        risk_per_trade_pct: Fixed risk per trade as fraction of pool.

    Returns:
        Dict with:
            position_size: Size in base currency (0 if no trade).
            risk_amount: USD amount at risk.
            kelly_fraction_used: The fractional Kelly value used.
            size_method: "kelly" or "fixed" or "zero".
    """
    if kelly_fraction is None:
        kelly_fraction = _DEFAULT_KELLY_FRACTION
    if max_position_fraction is None:
        max_position_fraction = _DEFAULT_MAX_POSITION_PCT
    if min_position_usd is None:
        min_position_usd = _DEFAULT_MIN_POSITION_USD
    if risk_per_trade_pct is None:
        risk_per_trade_pct = 0.02  # 2% default

    # Method 1: Kelly-based sizing
    kelly_pct = fractional_kelly(win_rate, avg_win_pct, avg_loss_pct,
                                  kelly_fraction=kelly_fraction)

    # Method 2: Fixed risk sizing (distance-based)
    price_diff = abs(entry_price - stop_loss)
    if price_diff > 0 and entry_price > 0:
        fixed_risk_usd = pool_balance * risk_per_trade_pct
        fixed_position_usd = fixed_risk_usd / (price_diff / entry_price)
        fixed_size = fixed_position_usd / entry_price
    else:
        fixed_size = 0
        fixed_position_usd = 0

    # Take the minimum of Kelly and fixed for conservatism
    if kelly_pct > 0:
        kelly_position_usd = pool_balance * kelly_pct
        kelly_size = kelly_position_usd / entry_price if entry_price > 0 else 0

        # Use the more conservative of Kelly and fixed
        if kelly_size > 0 and fixed_size > 0:
            if kelly_size <= fixed_size:
                position_size = kelly_size
                size_method = "kelly"
                kelly_fraction_used = kelly_pct
            else:
                position_size = fixed_size
                size_method = "fixed"
                kelly_fraction_used = 0.0  # Not using Kelly here
        elif kelly_size > 0:
            position_size = kelly_size
            size_method = "kelly"
            kelly_fraction_used = kelly_pct
        else:
            position_size = fixed_size
            size_method = "fixed"
            kelly_fraction_used = 0.0
    else:
        position_size = fixed_size
        size_method = "fixed"
        kelly_fraction_used = 0.0

    # Apply max position fraction cap
    max_usd = pool_balance * max_position_fraction
    max_size = max_usd / entry_price if entry_price > 0 else 0
    if max_size > 0 and position_size > max_size:
        logger.info(
            "Position size capped: %.8f -> %.8f (max %.1f%% of pool)",
            position_size, max_size, max_position_fraction * 100,
        )
        position_size = max_size

    # Enforce minimum position
    position_usd = position_size * entry_price if entry_price > 0 else 0
    if position_usd < min_position_usd and position_usd > 0:
        logger.info(
            "Position below minimum ($%.2f < $%.2f). Skipping.",
            position_usd, min_position_usd,
        )
        return {
            "position_size": 0.0,
            "risk_amount": 0.0,
            "kelly_fraction_used": kelly_fraction_used,
            "size_method": "zero",
            "reason": "below_minimum",
        }

    # Calculate actual risk
    if price_diff > 0 and entry_price > 0:
        risk_pct = price_diff / entry_price
        risk_amount = position_size * entry_price * risk_pct
    else:
        risk_amount = 0.0

    logger.info(
        "Position size: %s method=%.2f%%, size=%.6f, risk=$%.2f",
        f"pool={pool_balance:.2f}, entry={entry_price:.4f}, "
        f"sl={stop_loss:.4f}",
        kelly_fraction_used * 100,
        position_size,
        risk_amount,
    )

    return {
        "position_size": position_size,
        "risk_amount": risk_amount,
        "kelly_fraction_used": kelly_fraction_used,
        "size_method": size_method,
    }
