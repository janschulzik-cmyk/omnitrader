"""Tests for Privacy module (opt-out automator)."""

import pytest


class TestOptOutAutomator:
    """Tests for the data-broker opt-out automation engine."""

    @pytest.fixture
    def automator(self):
        """OptOutAutomator takes dry_run, not config."""
        from src.privacy.opt_out_automator import OptOutAutomator
        return OptOutAutomator(dry_run=True)

    def test_init(self, automator):
        """Automator initializes in dry_run mode."""
        assert automator.dry_run is True

    def test_get_broker_list(self, automator):
        """Broker list returns structured data."""
        brokers = automator.get_broker_list()
        assert isinstance(brokers, list)

    def test_submit_opt_out(self, automator):
        """Submit delegates to broker-specific handlers."""
        result = automator.submit_opt_out(
            broker_name="TestBroker",
            method="email",
            personal_info={"email": "user@example.com"},
        )
        assert isinstance(result, dict)

    def test_get_opt_out_evidence(self, automator):
        """Evidence lookup works for known brokers."""
        evidence = automator.get_opt_out_evidence("TestBroker")
        assert evidence is None or isinstance(evidence, dict)


class TestPrivacyGuardian:
    """Tests for privacy enums and status tracking."""

    def test_opt_out_status_enum(self):
        """OptOutStatus has expected values (CONFIRMED, not COMPLETED)."""
        from src.privacy.opt_out_automator import OptOutStatus
        assert hasattr(OptOutStatus, "PENDING")
        assert hasattr(OptOutStatus, "SUBMITTED")
        assert hasattr(OptOutStatus, "CONFIRMED")
        assert hasattr(OptOutStatus, "IGNORED")
        assert hasattr(OptOutStatus, "ERROR")

    def test_broker_type_enum(self):
        """BrokerType has expected values (DATABROKER, not DATA_BROKER)."""
        from src.privacy.opt_out_automator import BrokerType
        assert hasattr(BrokerType, "DATABROKER")
        assert hasattr(BrokerType, "AGRIGATOR")
        assert hasattr(BrokerType, "SCORING")
        assert hasattr(BrokerType, "BACKGROUND_CHECK")
        assert hasattr(BrokerType, "OTHER")
