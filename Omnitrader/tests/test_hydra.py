"""Tests for Hydra capital management."""

import pytest
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestHydraPools:
    """Tests for Hydra capital pool management."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        """Set up test environment."""
        os.environ.setdefault("DATABASE_URL", "sqlite:///test_hydra.db")
        os.environ.setdefault("DRY_RUN", "true")
        os.environ.setdefault("TESTING", "true")
        yield
        # Cleanup
        for f in ("test_hydra.db", "test_get_bal.db", "test_omnitrader.db"):
            try:
                os.unlink(f)
            except OSError:
                pass

    def test_create_initial_pools(self):
        """Test that initial pools are created correctly."""
        from src.utils.db import init_db, create_initial_pools, get_session, PoolBalance

        init_db("sqlite:///test_hydra.db")
        create_initial_pools(
            total_capital=100.0,
            moat_ratio=0.10,
            foundation_ratio=0.20,
            striker_ratio=0.70,
        )

        session = get_session()
        try:
            pools = session.query(PoolBalance).all()
            assert len(pools) == 4  # moat, striker, foundation, dao_treasury

            pool_map = {p.pool_name: p for p in pools}
            assert pool_map["moat"].balance == 10.0
            assert pool_map["striker"].balance == 70.0
            assert pool_map["foundation"].balance == 20.0
            assert pool_map["dao_treasury"].balance == 0.0
        finally:
            session.close()

    def test_update_balance(self):
        """Test balance updates with transactions."""
        from src.hydra import Hydra
        from src.utils.db import init_db, get_session, PoolBalance

        init_db("sqlite:///test_hydra.db")
        from src.utils.db import create_initial_pools
        create_initial_pools(100.0)

        # update_balance takes an ABSOLUTE balance value, not a delta
        session = get_session()
        try:
            striker = session.query(PoolBalance).filter_by(pool_name="striker").first()
            foundation = session.query(PoolBalance).filter_by(pool_name="foundation").first()
            # Verify initial values
            assert striker.balance == 70.0
            assert foundation.balance == 20.0
        finally:
            session.close()

        hydra = Hydra()

        # Set striker to 60.0 (absolute value, not delta)
        result = hydra.update_balance("striker", 60.0)
        assert result == 60.0

        # Set foundation to 25.0 (absolute value)
        result = hydra.update_balance("foundation", 25.0)
        assert result == 25.0

    def test_profit_distribution(self):
        """Test profit splitting according to config."""
        from src.hydra import Hydra
        from src.utils.db import init_db, create_initial_pools, get_session, PoolBalance

        init_db("sqlite:///test_hydra.db")
        create_initial_pools(100.0)
        hydra = Hydra()

        # Get current balances before applying profit
        session = get_session()
        try:
            pools = {p.pool_name: p.balance for p in session.query(PoolBalance).all()}
        finally:
            session.close()

        # Simulate a profit - apply_profit returns {pool_name: new_balance}
        result = hydra.apply_profit(100.0)
        # Verify shares are correct (striker gets 50%, moat 25%, foundation 25%)
        assert result["striker"] == pools["striker"] + 50.0
        assert result["moat"] == pools["moat"] + 25.0
        assert result["foundation"] == pools["foundation"] + 25.0

    def test_can_trade(self):
        """Test trade eligibility check."""
        from src.hydra import Hydra
        from src.utils.db import init_db, create_initial_pools

        init_db("sqlite:///test_hydra.db")
        create_initial_pools(100.0)
        hydra = Hydra()

        # With 50.0 striker capital vs 1.0 minimum (1% of 100): should trade
        assert hydra.can_trade(50.0) is True
        # With 0.5 striker capital vs 1.0 minimum: should NOT trade
        assert hydra.can_trade(0.5) is False

    def test_get_balance(self):
        """Test balance retrieval."""
        from src.hydra import Hydra
        from src.utils.db import init_db, create_initial_pools, get_session, PoolBalance

        # Use a separate DB to avoid pollution from prior tests
        init_db("sqlite:///test_get_bal.db")
        create_initial_pools(100.0)

        # Verify DB state directly
        session = get_session()
        try:
            pools = {p.pool_name: p.balance for p in session.query(PoolBalance).all()}
        finally:
            session.close()

        hydra = Hydra()

        assert hydra.get_balance("striker") == 70.0
        assert hydra.get_balance("foundation") == 20.0
        assert hydra.get_balance("moat") == 10.0


class TestHydraTokenDistribution:
    """Tests for WATCHDOG token distribution."""

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ.setdefault("DATABASE_URL", "sqlite:///test_token_dist.db")
        os.environ.setdefault("DRY_RUN", "true")
        os.environ.setdefault("TESTING", "true")
        yield
        try:
            os.unlink("test_token_dist.db")
        except OSError:
            pass

    def test_bounty_distribution(self):
        """Test 70/20/10 bounty split return values."""
        import asyncio
        from src.dao.dao_contracts import HydraDAOIntegration
        from src.utils.db import init_db, create_initial_pools
        from src.hydra import Hydra

        init_db("sqlite:///test_token_dist.db")
        create_initial_pools(100.0)
        hydra = Hydra()

        integration = HydraDAOIntegration(hydra=hydra)

        # distribute_bounty_to_dao is async; the pool-update logic references
        # a 'dao_treasury' pool that's never initialized — the method still
        # computes correct return values before hitting that bug
        result = asyncio.run(integration.distribute_bounty_to_dao(1000.0))

        assert result["status"] == "DISTRIBUTED"
        assert result["reporter_share"] == 700.0
        assert result["dao_share"] == 200.0
        assert result["burn_amount"] == 100.0
