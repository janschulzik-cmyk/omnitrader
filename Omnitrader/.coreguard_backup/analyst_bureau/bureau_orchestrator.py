"""Analyst Bureau Orchestrator - wraps TradingAgents for Omnitrader."""

import os
from typing import Dict, Optional, Any

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

from ..utils.logging_config import get_logger

logger = get_logger("analyst_bureau.orchestrator")


class BureauOrchestrator:
    """Orchestrates the TradingAgents multi-agent analysis pipeline."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = DEFAULT_CONFIG.copy()
        if config:
            self.config.update(config)
        # Allow override of LLM API keys from environment
        self.config["llm_api_key"] = os.environ.get("LLM_API_KEY", self.config.get("llm_api_key", ""))
        self.config["llm_base_url"] = os.environ.get("LLM_BASE_URL", self.config.get("llm_base_url", ""))
        self.graph: Optional[TradingAgentsGraph] = None
        self.decisions = []

    def initialize(self) -> None:
        """Initialize the TradingAgents graph."""
        if self.graph is None:
            try:
                self.graph = TradingAgentsGraph(debug=False, config=self.config)
                logger.info("TradingAgents graph initialized")
            except Exception as e:
                logger.error("Failed to initialize TradingAgents graph: %s", e)
                raise

    def analyze(self, ticker: str, date: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Run the full TradingAgents analysis on a ticker.

        Args:
            ticker: Stock/crypto ticker symbol (e.g., SOL, BTC)
            date: Date string in format YYYY-MM-DD
            context: Additional context about the signal

        Returns:
            Decision dictionary with keys: decision, analyst_signals, risk_assessment, debate_summary
        """
        if not self.graph:
            self.initialize()
        logger.info("Starting Bureau analysis for %s on %s", ticker, date)
        try:
            # TradingAgentsGraph.propagate returns (report, decision)
            report, decision = self.graph.propagate(ticker, date)
            result = {
                "ticker": ticker,
                "date": date,
                "decision": decision,
                "report": report,
                "context": context or {},
            }
            self.decisions.append(result)
            logger.info("Bureau analysis complete: decision=%s", decision.get("action", "unknown"))
            return result
        except Exception as e:
            logger.error("Bureau analysis failed: %s", e)
            raise

    def get_last_decision(self) -> Optional[Dict]:
        """Return the most recent decision."""
        return self.decisions[-1] if self.decisions else None

    def clear_history(self) -> None:
        """Clear stored decisions."""
        self.decisions = []
