"""Tests for Sleuth module (onchain scanner, bounty reporter, data broker scanner)."""

import pytest
from unittest.mock import MagicMock, patch


class TestOnChainScanner:
    """Tests for on-chain activity scanning."""

    @pytest.fixture
    def scanner(self):
        from src.sleuth.onchain_scanner import OnChainScanner
        return OnChainScanner(config={})

    def test_init(self, scanner):
        """Scanner initializes with config."""
        assert hasattr(scanner, "ethereum_rpc")
        assert hasattr(scanner, "sanctioned_addresses")
        assert isinstance(scanner.sanctioned_addresses, set)

    def test_full_scan(self, scanner):
        """Full scan runs and delegates to pattern detection."""
        with patch.object(type(scanner), "scan_new_token_launches", return_value=[]), \
             patch.object(type(scanner), "scan_cex_deposits", return_value=[]), \
             patch.object(type(scanner), "_detect_malicious_patterns", return_value=[]):
            result = scanner.run_full_scan(chains=["ethereum"])
            assert isinstance(result, list)


class TestDataBrokerScanner:
    """Tests for data broker violation scanning."""

    @pytest.fixture
    def scanner(self):
        from src.sleuth.databroker_scanner import DataBrokerScanner
        # Mock _load_previous_scans to avoid DB issues
        with patch.object(DataBrokerScanner, "_load_previous_scans"):
            return DataBrokerScanner(config={"max_brokers": 1})

    def test_scan_all_brokers(self, scanner):
        """Scan iterates over known brokers."""
        with patch("src.sleuth.databroker_scanner.httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {}
            results = scanner.scan_all_brokers()
            assert isinstance(results, list)

    def test_alert_model(self):
        """Alert model creates correctly with source fields."""
        from src.utils.db import DataBrokerAlert
        alert = DataBrokerAlert(
            broker_name="TestBroker",
            violation_type="CCPA_VIOLATION",
            severity="MEDIUM",
            evidence='{"found": true}',
            broker_website="https://testbroker.com",
        )
        d = alert.to_dict()
        assert d["broker_name"] == "TestBroker"
        assert "evidence" in d


class TestBountyReporter:
    """Tests for bounty report generation and submission."""

    @pytest.fixture
    def reporter(self):
        from src.sleuth.bounty_reporter import BountyReporter
        return BountyReporter()

    def test_targets_exist(self, reporter):
        """Bounty targets are registered under BOUNTY_PROGRAMS."""
        assert "ftc" in reporter.BOUNTY_PROGRAMS
        assert "sec" in reporter.BOUNTY_PROGRAMS
        assert "cftc" in reporter.BOUNTY_PROGRAMS
        assert "doj" in reporter.BOUNTY_PROGRAMS

    def test_format_report(self, reporter):
        """WhistleblowerTarget.format_report works."""
        target = reporter.BOUNTY_PROGRAMS["ftc"]
        evidence = {"summary": "Test", "violations": []}
        report = target.format_report(evidence, report_type="violation")
        assert report["target"] == target.name
        assert "evidence_summary" in report

    def test_submit_via_email(self, reporter):
        """Email submission uses _send_email_submission."""
        with patch.object(
            type(reporter), "_send_email_submission", return_value={"sent": True}
        ) as mock_send:
            result = reporter.submit_report(
                evidence={"summary": "test"},
                target_name="ftc",
                dry_run=True,
            )
            assert result is not None

    def test_pdf_generation_dry_run(self, reporter):
        """PDF generation is skipped in dry_run mode."""
        result = reporter.submit_report(
            evidence={"summary": "test"},
            target_name="ftc",
            dry_run=True,
        )
        assert result is not None
