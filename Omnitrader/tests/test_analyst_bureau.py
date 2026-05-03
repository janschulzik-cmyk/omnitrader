"""Tests for Analyst Bureau module."""

import os
import sys
import json
from unittest.mock import MagicMock, patch

import pytest

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATABASE_URL", "sqlite:///test_omnitrader.db")


class TestBureauOrchestrator:
    def test_init(self):
        from src.analyst_bureau.bureau_orchestrator import BureauOrchestrator
        bureau = BureauOrchestrator()
        assert bureau.graph is None
        assert bureau.config is not None

    @patch("src.analyst_bureau.bureau_orchestrator.TradingAgentsGraph")
    def test_initialize(self, mock_graph_class):
        from src.analyst_bureau.bureau_orchestrator import BureauOrchestrator
        bureau = BureauOrchestrator()
        mock_graph = MagicMock()
        mock_graph_class.return_value = mock_graph
        bureau.initialize()
        assert bureau.graph is not None
        mock_graph_class.assert_called_once()

    @patch("src.analyst_bureau.bureau_orchestrator.TradingAgentsGraph")
    def test_analyze(self, mock_graph_class):
        from src.analyst_bureau.bureau_orchestrator import BureauOrchestrator
        bureau = BureauOrchestrator()
        mock_graph = MagicMock()
        mock_graph.propagate.return_value = ("report", {"action": "buy", "confidence": 0.8})
        mock_graph_class.return_value = mock_graph
        result = bureau.analyze("SOL", "2026-05-03")
        assert result["decision"]["action"] == "buy"
        assert len(bureau.decisions) == 1


class TestSignalEnrichment:
    def test_map_pair_to_ticker(self):
        from src.analyst_bureau.signal_enrichment import map_pair_to_ticker
        assert map_pair_to_ticker("SOL/USDT") == "SOL"
        assert map_pair_to_ticker("BTC/USDT") == "BTC"

    @patch("src.analyst_bureau.signal_enrichment.BureauOrchestrator")
    def test_enrich_signal(self, mock_bureau_class):
        from src.analyst_bureau.signal_enrichment import enrich_signal
        mock_bureau = MagicMock()
        mock_bureau.analyze.return_value = {
            "decision": {"action": "short", "confidence": 0.9, "risk_level": "medium", "summary": "Bearish consensus"},
            "report": {}
        }
        signal = {
            "pair": "SOL/USDT",
            "signal_type": "SHORT",
            "entry_price": 100,
            "stop_loss": 105,
            "take_profit": 90,
        }
        enriched = enrich_signal(signal, mock_bureau)
        assert enriched["analyst_consensus"] == "sell"
        assert enriched["bureau_approved"] is True
        assert "confidence_modifier" in enriched
        assert "risk_adjustment" in enriched


class TestDecisionFusion:
    def test_fuse_signal_approved(self):
        from src.analyst_bureau.decision_fusion import fuse_signal
        original = {"pair": "SOL/USDT", "entry_price": 100, "stop_loss": 105}
        enriched = {
            "pair": "SOL/USDT",
            "entry_price": 100,
            "stop_loss": 105,
            "analyst_consensus": "sell",
            "confidence_modifier": 1.3,
            "risk_adjustment": 0.8,
            "bureau_approved": True,
            "debate_summary": "Strong sell"
        }
        result = fuse_signal(original, enriched, 1000.0)
        assert result is not None
        assert result["risk_per_trade_pct"] > 0

    def test_fuse_signal_rejected(self, monkeypatch):
        monkeypatch.setenv("ANALYST_BUREAU_ENABLED", "true")
        from src.analyst_bureau.decision_fusion import fuse_signal
        original = {"pair": "SOL/USDT", "entry_price": 100, "stop_loss": 105}
        enriched = {
            "pair": "SOL/USDT",
            "entry_price": 100,
            "stop_loss": 105,
            "bureau_approved": False,
            "debate_summary": "Too risky"
        }
        result = fuse_signal(original, enriched, 1000.0)
        assert result is None  # should cancel

    def test_fuse_signal_bureau_disabled(self, monkeypatch):
        monkeypatch.setenv("ANALYST_BUREAU_ENABLED", "false")
        from src.analyst_bureau.decision_fusion import fuse_signal
        original = {"pair": "SOL/USDT", "entry_price": 100, "stop_loss": 105}
        enriched = {
            "pair": "SOL/USDT",
            "entry_price": 100,
            "stop_loss": 105,
            "bureau_approved": False,
            "debate_summary": "Too risky"
        }
        result = fuse_signal(original, enriched, 1000.0)
        assert result is not None  # Bureau disabled, so trade goes through
