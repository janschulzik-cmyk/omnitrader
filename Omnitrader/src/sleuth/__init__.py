"""Sleuth Module: On-chain investigator and bounty hunter.

Scans blockchain for malicious activity, data broker violations,
and submits evidence to bounty programs.
"""

from .onchain_scanner import OnChainScanner
from .bounty_reporter import BountyReporter
from .databroker_scanner import DataBrokerScanner

__all__ = ["OnChainScanner", "BountyReporter", "DataBrokerScanner"]
