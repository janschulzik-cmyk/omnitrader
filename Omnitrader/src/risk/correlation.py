"""Correlation-based position sizing adjustment.

Before entering a new trade, retrieves recent price data for existing
positions and the new signal's pair, computes Pearson correlation
over the last N candles, and reduces position size if correlation
is high and directional risk would increase.
"""

import os
import math
from typing import Dict, List, Optional, Tuple

import ccxt
import httpx

from ..utils.logging_config import get_logger

logger = get_logger("risk.correlation")

# ── Default parameters ────────────────────────────────────────────────

_DEFAULT_CORRELATION_THRESHOLD = float(
    os.environ.get("CORRELATION_THRESHOLD", "0.7")
)
_DEFAULT_CORRELATION_WINDOW = int(os.environ.get("CORRELATION_WINDOW", "50"))
_DEFAULT_CORR_SIZE_REDUCTION = float(
    os.environ.get("CORR_SIZE_REDUCTION", "0.5")
)  # Reduce to 50% if correlated


# ── Price data helpers ────────────────────────────────────────────────

def _normalize_pair(pair: str) -> str:
    """Normalize trading pair format (e.g., SOL/USDT)."""
    return pair.replace("USDT", "USDT").replace("USD", "USD").upper()


def fetch_ohlcv(ccxt_exchange, symbol: str, timeframe: str = "15m",
                limit: int = 100) -> List[List[float]]:
    """Fetch OHLCV data via ccxt.

    Args:
        ccxt_exchange: ccxt exchange instance.
        symbol: Trading pair.
        timeframe: Candle timeframe.
        limit: Number of candles.

    Returns:
        List of [timestamp, open, high, low, close, volume].
    """
    try:
        if ccxt_exchange:
            data = ccxt_exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return data
    except Exception as e:
        logger.warning("ccxt fetch_ohlcv failed for %s: %s", symbol, e)
    return []


def extract_closes(ohlcv_data: List[List[float]]) -> List[float]:
    """Extract closing prices from OHLCV data.

    Args:
        ohlcv_data: List of [ts, open, high, low, close, volume].

    Returns:
        List of close prices (most recent last).
    """
    if not ohlcv_data:
        return []
    return [candle[4] for candle in ohlcv_data]  # index 4 = close


def pearson_correlation(x: List[float], y: List[float]) -> Optional[float]:
    """Compute Pearson correlation coefficient between two series.

    Args:
        x: First series of values.
        y: Second series of values (same length).

    Returns:
        Pearson correlation (-1.0 to 1.0), or None if insufficient data.
    """
    n = min(len(x), len(y))
    if n < 10:  # Need at least 10 data points
        return None

    x = x[:n]
    y = y[:n]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    denom_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    denom_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))

    if denom_x == 0 or denom_y == 0:
        return 0.0

    return numerator / (denom_x * denom_y)


# ── Main correlation check ────────────────────────────────────────────

def check_correlation(
    open_positions: List[Dict],
    new_signal: Dict,
    ccxt_exchange=None,
    correlation_threshold: float = None,
    correlation_window: int = None,
    size_reduction: float = None,
) -> Dict:
    """Check correlation between existing positions and a new signal.

    If the absolute correlation exceeds the threshold AND the new trade
    would increase directional risk (e.g., already short BTC and about
    to short ETH), reduce the position size.

    Args:
        open_positions: List of open position dicts with 'pair' and 'side'.
        new_signal: Signal dict with 'pair' and 'signal_type'.
        ccxt_exchange: Optional ccxt exchange for live OHLCV fetch.
        correlation_threshold: Correlation threshold (default 0.7).
        correlation_window: Number of candles for correlation (default 50).
        size_reduction: Fraction to reduce to (default 0.5 = 50%).

    Returns:
        Dict with:
            correlation: Pearson correlation coefficient.
            is_highly_correlated: True if threshold exceeded.
            size_multiplier: 1.0 if no reduction, <1.0 if reduced.
            adjustment_reason: Why the adjustment was made.
    """
    if correlation_threshold is None:
        correlation_threshold = _DEFAULT_CORRELATION_THRESHOLD
    if correlation_window is None:
        correlation_window = _DEFAULT_CORRELATION_WINDOW
    if size_reduction is None:
        size_reduction = _DEFAULT_CORR_SIZE_REDUCTION

    pair = new_signal.get("pair", "")
    signal_side = new_signal.get("signal_type", "")
    if not pair or not signal_side:
        return {
            "correlation": None,
            "is_highly_correlated": False,
            "size_multiplier": 1.0,
            "adjustment_reason": "insufficient_data",
        }

    if not open_positions:
        return {
            "correlation": None,
            "is_highly_correlated": False,
            "size_multiplier": 1.0,
            "adjustment_reason": "no_open_positions",
        }

    # Determine base currency to filter cross-pairs (e.g., all /USDT pairs)
    base = pair.rsplit("/", 1)[-1] if "/" in pair else "USDT"
    new_pair_base = _normalize_pair(pair)

    # Extract directional bias
    direction_map = {"SHORT": -1, "LONG": 1, "BUY": 1, "SELL": -1}
    new_direction = direction_map.get(signal_side, 0)

    # For each open position, check if it shares a base currency
    correlated_pairs = []
    for pos in open_positions:
        pos_pair = _normalize_pair(pos.get("pair", ""))
        pos_side = pos.get("side", "")
        pos_direction = direction_map.get(pos_side, 0)

        # Same base currency check (both /USDT, both /BTC, etc.)
        pos_base = pos_pair.rsplit("/", 1)[-1] if "/" in pos_pair else ""
        if pos_base != base:
            continue

        # Check directional risk: same direction = correlated risk
        same_direction = new_direction != 0 and pos_direction != 0 and \
                         (new_direction * pos_direction > 0)

        correlated_pairs.append({
            "pair": pos_pair,
            "direction": pos_direction,
            "same_direction": same_direction,
        })

    if not correlated_pairs:
        return {
            "correlation": None,
            "is_highly_correlated": False,
            "size_multiplier": 1.0,
            "adjustment_reason": "different_base_currency",
        }

    # Fetch OHLCV data for the new pair and correlated positions
    new_closes = extract_closes(
        fetch_ohlcv(ccxt_exchange, pair, limit=correlation_window)
    )

    max_corr = 0.0
    reason = "no_correlation"

    for cp in correlated_pairs:
        pos_closes = extract_closes(
            fetch_ohlcv(ccxt_exchange, cp["pair"], limit=correlation_window)
        )

        if len(new_closes) < 10 or len(pos_closes) < 10:
            continue

        corr = pearson_correlation(new_closes, pos_closes)
        if corr is None:
            continue

        abs_corr = abs(corr)
        if abs_corr > max_corr:
            max_corr = abs_corr
            reason = (
                f"correlated_with_{cp['pair']}"
                f"(corr={corr:.3f}, same_dir={cp['same_direction']})"
            )

    # Determine if position size should be reduced
    size_multiplier = 1.0
    if max_corr > correlation_threshold:
        # Only reduce if same directional risk
        if reason and "same_dir=True" in reason:
            size_multiplier = size_reduction
            logger.warning(
                "Correlation alert: %.3f exceeds threshold %.1f. "
                "Reducing position to %.0f%%.",
                max_corr, correlation_threshold, size_multiplier * 100,
            )
        else:
            logger.info(
                "High correlation %.3f with %s, but different direction. "
                "No size adjustment.",
                max_corr, reason,
            )

    return {
        "correlation": max_corr,
        "is_highly_correlated": max_corr > correlation_threshold,
        "size_multiplier": size_multiplier,
        "adjustment_reason": reason,
    }
