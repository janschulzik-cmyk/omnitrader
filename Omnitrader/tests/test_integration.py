"""Integration / smoke tests for Omnitrader.

Run with: pytest tests/test_integration.py -v
"""

import os
import sys
import pytest
from pathlib import Path

# Set up environment
os.environ.setdefault("DATABASE_URL", "sqlite:///test_integration.db")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456")
os.environ.setdefault("NEWSAPI_KEY", "test-newsapi")
os.environ.setdefault("LLM_API_KEY", "test-llm")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter")
os.environ.setdefault("BINANCE_API_KEY", "test-binance-key")
os.environ.setdefault("BINANCE_API_SECRET", "test-binance-secret")
os.environ.setdefault("MASTER_PASSPHRASE", "test-passphrase")
os.environ.setdefault("FERNET_KEY", "test-fernet-key")
os.environ.setdefault("BINANCE_TESTNET", "true")
os.environ.setdefault("INTELLIGENCE_ENABLED", "true")
os.environ.setdefault("CRYPTO_API_KEY", "test-crypto-key")
os.environ.setdefault("CRYPTO_API_SECRET", "test-crypto-secret")
os.environ.setdefault("INTELLIGENCE_API_KEY", "test-llm-key")

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestImports:
    """Verify all modules import without errors."""

    def test_core_imports(self):
        """Core modules import cleanly."""
        from src import hydra
        assert hydra is not None

    def test_striker_imports(self):
        """Striker module imports."""
        from src.striker.news_monitor import NewsMonitor
        from src.striker.mean_reversion import MeanReversionSignalGenerator
        from src.striker.trade_executor import TradeExecutor
        assert NewsMonitor is not None
        assert MeanReversionSignalGenerator is not None
        assert TradeExecutor is not None

    def test_foundation_imports(self):
        """Foundation module imports."""
        from src.foundation.politician_tracker import PoliticianTracker
        from src.foundation.dividend_portfolio import DividendPortfolio
        from src.foundation.rebalancer import Rebalancer
        assert PoliticianTracker is not None
        assert DividendPortfolio is not None
        assert Rebalancer is not None

    def test_sleuth_imports(self):
        """Sleuth module imports."""
        from src.sleuth.onchain_scanner import OnChainScanner
        from src.sleuth.databroker_scanner import DataBrokerScanner
        from src.sleuth.bounty_reporter import BountyReporter
        assert OnChainScanner is not None
        assert DataBrokerScanner is not None
        assert BountyReporter is not None

    def test_legal_imports(self):
        """Legal module imports."""
        from src.legal.arbitration_draft import LegalDraftingEngine
        from src.legal.filing_dispatcher import FilingDispatcher
        assert LegalDraftingEngine is not None
        assert FilingDispatcher is not None

    def test_dao_imports(self):
        """DAO module imports."""
        from src.dao.dao_contracts import HydraDAOIntegration
        from src.dao.token import WATCHDOG_NAME, WATCHDOG_SYMBOL
        from src.dao.governance import ProposalType
        assert HydraDAOIntegration is not None
        assert WATCHDOG_NAME == "Watchdog Token"
        assert WATCHDOG_SYMBOL == "WDOG"

    def test_privacy_imports(self):
        """Privacy module imports."""
        from src.privacy.opt_out_automator import OptOutAutomator
        assert OptOutAutomator is not None

    def test_intelligence_imports(self):
        """Intelligence module imports."""
        from src.intelligence.llm_interface import LLMInterface
        from src.intelligence.learning_loop import LearningLoop
        assert LLMInterface is not None
        assert LearningLoop is not None

    def test_apis_imports(self):
        """APIs module imports."""
        from src.apis.routes import router
        from src.apis.telegram_bot import TelegramBot
        from src.apis.auth import API_KEY
        assert router is not None
        assert TelegramBot is not None
        assert API_KEY == "test-api-key"

    def test_utils_imports(self):
        """Utils module imports."""
        from src.utils.logging_config import setup_logging
        from src.utils.db import init_db, get_session
        from src.utils.security import get_encrypted_exchange_keys
        assert setup_logging is not None
        assert init_db is not None
        assert get_encrypted_exchange_keys is not None


class TestDatabase:
    """Test database initialization and operations."""

    def test_init_db(self):
        """Database initializes without error."""
        from src.utils.db import init_db, Base
        import src.utils.db as db

        init_db("sqlite:///test_integration.db")
        # Verify engine was created by checking the module's _engine
        assert db._engine is not None

    def test_create_initial_pools(self):
        """Initial pools are created."""
        from src.utils.db import create_initial_pools, get_session, PoolBalance

        create_initial_pools(
            total_capital=100.0,
            moat_ratio=0.10,
            foundation_ratio=0.20,
            striker_ratio=0.70,
        )

        session = get_session()
        try:
            pools = session.query(PoolBalance).all()
            pool_names = {p.pool_name for p in pools}
            # dao_treasury is also created by create_initial_pools
            assert "moat" in pool_names
            assert "striker" in pool_names
            assert "foundation" in pool_names
        finally:
            session.close()

    def test_system_event_logging(self):
        """System events can be logged."""
        from src.utils.db import log_system_event, get_session, SystemEvent

        # Count events before
        session = get_session()
        try:
            before_count = session.query(SystemEvent).count()
        finally:
            session.close()

        event = log_system_event(
            event_type="TEST_EVENT",
            message="Test message",
            module="integration",
            details={"key": "value"},
        )
        assert event is not None

        # Count events after (event is a detached instance, so we can't
        # access its attributes directly - verify by count instead)
        session = get_session()
        try:
            after_count = session.query(SystemEvent).count()
            assert after_count >= before_count + 1
        finally:
            session.close()


class TestSecurity:
    """Test security utilities."""

    def test_encryption_roundtrip(self):
        """Encrypt and decrypt produces original value."""
        from src.utils.security import encrypt_string, decrypt_string

        original = "my-secret-key"
        encrypted = encrypt_string(original)
        decrypted = decrypt_string(encrypted)
        assert decrypted == original

    def test_nonce_generation(self):
        """Nonce is generated and is unique."""
        from src.utils.security import generate_nonce

        nonce1 = generate_nonce()
        nonce2 = generate_nonce()
        assert nonce1 != nonce2
        # generate_nonce() produces a 32-byte hex nonce = 64 chars
        assert len(nonce1) == 64


class TestDryRunMode:
    """Test that Hydra initialization works correctly."""

    def test_hydra_init(self):
        """Hydra initializes with correct capital allocation."""
        from src.hydra import Hydra
        from src.utils.db import init_db, create_initial_pools

        init_db("sqlite:///test_integration.db")
        create_initial_pools(100.0)

        hydra = Hydra()
        # Hydra should have non-zero pool allocations
        assert hydra.striker > 0
        assert hydra.foundation > 0
        assert hydra.moat > 0
        # Total capital should match
        assert abs((hydra.striker + hydra.foundation + hydra.moat) - 100.0) < 0.01


class TestFastAPIApp:
    """Test FastAPI routes are registered."""

    def test_app_exists(self):
        """FastAPI router is created."""
        from src.apis.routes import router
        assert router is not None

    def test_routes_exist(self):
        """All expected routes are registered."""
        from src.apis.routes import router
        routes = [route.path for route in router.routes]
        assert "/api/v1/status" in routes
        assert "/api/v1/command" in routes


class TestConfiguration:
    """Test configuration loading."""

    def test_settings_load(self):
        """Logging config has expected functions."""
        from src.utils.logging_config import setup_logging
        assert setup_logging is not None
        # Verify it's callable
        assert callable(setup_logging)


class TestSmokeFull:
    """Full smoke test - instantiate all major components."""

    def test_instantiate_all_components(self):
        """All major components can be instantiated."""
        from unittest.mock import patch, MagicMock
        from pathlib import Path
        from src.hydra import Hydra
        from src.striker.news_monitor import NewsMonitor
        from src.striker.trade_executor import TradeExecutor
        from src.foundation.politician_tracker import PoliticianTracker
        from src.foundation.dividend_portfolio import DividendPortfolio
        from src.sleuth.onchain_scanner import OnChainScanner
        from src.sleuth.databroker_scanner import DataBrokerScanner
        from src.sleuth.bounty_reporter import BountyReporter
        from src.legal.arbitration_draft import LegalDraftingEngine
        from src.legal.filing_dispatcher import FilingDispatcher
        from src.privacy.opt_out_automator import OptOutAutomator
        from src.intelligence.llm_interface import LLMInterface
        from src.apis.telegram_bot import TelegramBot

        # Mock directory creation to avoid permission errors
        mock_mkdir = MagicMock()
        with patch.object(Path, "mkdir", mock_mkdir):
            hydra = Hydra()
            news = NewsMonitor()
            executor = TradeExecutor()
            tracker = PoliticianTracker()
            portfolio = DividendPortfolio()
            scanner = OnChainScanner()
            db_scanner = DataBrokerScanner()
            reporter = BountyReporter()
            drafter = LegalDraftingEngine()
            dispatcher = FilingDispatcher()
            optout = OptOutAutomator()
            llm = LLMInterface()
            telegram = TelegramBot(config={"token": "test", "chat_id": "123456"})

        # Verify they are the correct types
        assert type(hydra).__name__ == "Hydra"
        assert type(news).__name__ == "NewsMonitor"
        assert type(reporter).__name__ == "BountyReporter"
        assert type(drafter).__name__ == "LegalDraftingEngine"
        assert type(optout).__name__ == "OptOutAutomator"


class TestCleanup:
    """Cleanup test database after tests."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        yield
        import os
        for db in ["test_integration.db", "test_hydra.db", "test_omnitrader.db"]:
            try:
                os.unlink(db)
            except OSError:
                pass
