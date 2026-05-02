"""Shared test fixtures for Omnitrader test suite."""

import os
import sys
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set test environment variables before imports
os.environ.setdefault("DATABASE_URL", "sqlite:///test_omnitrader.db")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("TELEGRAM_TOKEN", "test-telegram-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456")
os.environ.setdefault("NEWSAPI_KEY", "test-newsapi-key")
os.environ.setdefault("LLM_API_KEY", "test-llm-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("BINANCE_API_KEY", "test-binance-key")
os.environ.setdefault("BINANCE_API_SECRET", "test-binance-secret")
os.environ.setdefault("MASTER_PASSPHRASE", "test-passphrase")
os.environ.setdefault("FERNET_KEY", "test-fernet-key")


@pytest.fixture
def temp_db():
    """Provide a temporary SQLite database for tests."""
    from src.utils.db import init_db, get_session, Base

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_url = f"sqlite:///{tmp.name}"

    # Patch DATABASE_URL
    import src.utils.db as db_module
    original_url = db_module.DATABASE_URL
    db_module.DATABASE_URL = db_url
    db_module._engine = None
    db_module._session_factory = None

    init_db(db_url)
    yield db_url

    # Cleanup
    db_module._engine = None
    db_module._session_factory = None
    db_module.DATABASE_URL = original_url
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


@pytest.fixture
def sample_trade_data():
    """Provide sample trade data."""
    return {
        "pair": "SOL/USDT",
        "side": "SELL",
        "entry_price": 150.0,
        "stop_loss": 157.5,
        "quantity": 10.0,
        "risk_amount": 75.0,
        "trigger_fear_score": 85,
        "trigger_headline": "Fed raises rates unexpectedly",
        "volume_anomaly": True,
        "candle_pattern": "shooting_star",
    }


@pytest.fixture
def mock_ccxt_exchange():
    """Provide a mock ccxt exchange object."""
    exchange = MagicMock()
    exchange.fetch_order_book.return_value = {
        "bids": [[149.5, 100]],
        "asks": [[150.5, 100]],
    }
    exchange.create_order.return_value = {
        "id": "test_order_123",
        "status": "closed",
        "filled": 10.0,
        "average": 150.0,
    }
    exchange.fetch_ohlcv.return_value = [
        [1600000000000, 151.0, 152.0, 149.0, 150.0, 1000],
        [1600000090000, 145.0, 150.0, 140.0, 142.0, 5000],
    ]
    return exchange


@pytest.fixture
def mock_llm_interface():
    """Provide a mock LLM interface."""
    llm = MagicMock()
    llm.call_llm.return_value = "Based on the data, the Striker module should prioritize high fear scores above 85."
    llm.generate_skill_update.return_value = "Updated striker instructions: Focus on SOL/USDT only when fear > 85."
    return llm


@pytest.fixture
def mock_hydra():
    """Provide a mock Hydra capital manager."""
    hydra = MagicMock()
    hydra.get_balance.return_value = 70.0
    hydra.update_balance.return_value = None
    hydra.apply_profit.return_value = None
    hydra.can_trade.return_value = True
    return hydra
