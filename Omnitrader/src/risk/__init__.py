"""Risk management subsystem for Omnitrader.

Provides:
  - Position sizing via fractional Kelly Criterion
  - Correlation-based position sizing adjustments
  - Circuit breaker for maximum drawdown protection
"""

from .position_sizer import fractional_kelly_size, calc_position_size
from .correlation import check_correlation

__all__ = ["fractional_kelly_size", "calc_position_size", "check_correlation"]
