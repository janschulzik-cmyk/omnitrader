"""Striker Module: Event-driven mean-reversion trader.

Exploits media overreactions through fear/greed spike detection
and mean-reversion signals on cryptocurrency pairs.
"""

from .news_monitor import NewsMonitor
from .mean_reversion import MeanReversionSignalGenerator
from .trade_executor import TradeExecutor

__all__ = ["NewsMonitor", "MeanReversionSignalGenerator", "TradeExecutor"]
