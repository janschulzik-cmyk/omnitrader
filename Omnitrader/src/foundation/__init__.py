"""Foundation Module: Long-term diversified portfolio manager.

Tracks congressional trades, manages high-dividend asset baskets,
and handles rebalancing across traditional and DeFi assets.
"""

from .politician_tracker import PoliticianTracker
from .dividend_portfolio import DividendPortfolio
from .rebalancer import Rebalancer

__all__ = ["PoliticianTracker", "DividendPortfolio", "Rebalancer"]
