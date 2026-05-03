"""Enriches Striker signals with Analyst Bureau multi-agent analysis."""

import os
from enum import Enum
from typing import Dict, Optional
from datetime import datetime, timezone

from .bureau_orchestrator import BureauOrchestrator
from ..utils.logging_config import get_logger

logger = get_logger("analyst_bureau.enrichment")


class SignalConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    REJECTED = "rejected"


def map_pair_to_ticker(pair: str) -> str:
    """Map a crypto trading pair to a ticker symbol TradingAgents can analyze.

    For well-known crypto assets, we use the asset name itself as ticker.
    """
    base = pair.split("/")[0]
    # Common mappings
    mapping = {
        "BTC": "BTC",
        "ETH": "ETH",
        "SOL": "SOL",
        "AVAX": "AVAX",
    }
    return mapping.get(base, base)


def enrich_signal(signal: Dict, orchestrator: BureauOrchestrator) -> Dict:
    """Enrich a Striker signal with Analyst Bureau analysis.

    Args:
        signal: Original Striker signal dict (must contain pair, signal_type, entry_price, stop_loss, take_profit)
        orchestrator: Initialized BureauOrchestrator instance

    Returns:
        Enriched signal dict with added fields:
        - analyst_consensus: buy/sell/hold
        - confidence_modifier: 0.5-1.5 multiplier
        - risk_adjustment: 0.5-1.5 multiplier
        - debate_summary: short text
        - bureau_approved: bool
        - analyst_report: full report dict (optional)
    """
    pair = signal.get("pair", "SOL/USDT")
    ticker = map_pair_to_ticker(pair)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info("Enriching signal for %s (ticker=%s)", pair, ticker)

    try:
        analysis = orchestrator.analyze(ticker, date, context=signal)
        decision = analysis.get("decision", {})
        report = analysis.get("report", {})

        # Determine consensus from the decision
        action = decision.get("action", "hold").lower()
        confidence = decision.get("confidence", 0.5)
        risk_level = decision.get("risk_level", "medium")

        # Map to multipliers
        if action in ("buy", "long"):
            analyst_consensus = "buy"
        elif action in ("sell", "short"):
            analyst_consensus = "sell"
        else:
            analyst_consensus = "hold"

        # Confidence modifier: scale 0.5 to 1.5 (default 1.0)
        confidence_modifier = 0.5 + confidence  # if confidence=0.5 -> 1.0; if confidence=1.0 -> 1.5
        confidence_modifier = max(0.5, min(1.5, confidence_modifier))

        # Risk adjustment: low risk -> smaller position, high risk -> larger position? Actually safer to scale down.
        risk_map = {"low": 1.2, "medium": 1.0, "high": 0.7, "critical": 0.5}
        risk_adjustment = risk_map.get(risk_level, 1.0)

        # Determine if approved
        min_confidence = float(os.environ.get("ANALYST_BUREAU_MIN_CONFIDENCE", 0.6))
        bureau_approved = confidence >= min_confidence and action != "hold"

        enriched = {
            **signal,
            "analyst_consensus": analyst_consensus,
            "confidence_modifier": confidence_modifier,
            "risk_adjustment": risk_adjustment,
            "debate_summary": decision.get("summary", ""),
            "bureau_approved": bureau_approved,
            "analyst_report": report,
        }

        logger.info("Signal enriched: consensus=%s, approved=%s, conf_mod=%.2f, risk_adj=%.2f",
                     analyst_consensus, bureau_approved, confidence_modifier, risk_adjustment)
        return enriched

    except Exception as e:
        logger.error("Signal enrichment failed: %s. Passing through original signal.", e)
        # If Bureau fails, do not block trading; return original signal with neutral modifiers
        return {
            **signal,
            "analyst_consensus": "hold",
            "confidence_modifier": 1.0,
            "risk_adjustment": 1.0,
            "debate_summary": "Analyst Bureau unavailable",
            "bureau_approved": True,  # Allow trade through on error
            "analyst_report": None,
        }
