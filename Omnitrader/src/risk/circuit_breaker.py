"""Circuit breaker for maximum drawdown protection.

Tracks the peak cumulative PnL of the entire system (across all pools)
and locks the Striker when drawdown from peak exceeds a configurable
threshold (default 20%).

Integrates with Hydra's balance system and provides manual reset
capability.
"""

import os
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

from ..utils.logging_config import get_logger

logger = get_logger("risk.circuit_breaker")

# ── Default parameters ────────────────────────────────────────────────

_DEFAULT_DRAWDOWN_THRESHOLD = float(
    os.environ.get("MAX_DRAWDOWN", "0.20")  # 20% default
)
_DEFAULT_DRAWDOWN_THRESHOLD_PCT = _DEFAULT_DRAWDOWN_THRESHOLD * 100


Base = declarative_base()


class CircuitBreakerState(Base):
    """Database model for circuit breaker state persistence."""

    __tablename__ = "circuit_breaker_state"

    id = Column(Integer, primary_key=True)
    peak_pnl = Column(Float, default=0.0)
    breaker_triggered = Column(String(16), default="OFF")
    triggered_at = Column(DateTime, nullable=True)
    triggered_at_ts = Column(Float, nullable=True)
    reset_at = Column(DateTime, nullable=True)
    reset_at_ts = Column(Float, nullable=True)
    total_trades = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    avg_win_pct = Column(Float, default=0.0)
    avg_loss_pct = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow)


def get_db_engine(db_url: str = None) -> object:
    """Get SQLAlchemy engine for the circuit breaker state table."""
    if db_url is None:
        db_url = os.environ.get(
            "DATABASE_URL",
            "sqlite:///data/omnitrader.db",
        )
    engine = create_engine(db_url, echo=False)
    CircuitBreakerState.__table__.create(engine, checkfirst=True)
    return engine


def _get_session(db_url: str = None):
    """Get a session for circuit breaker state."""
    engine = get_db_engine(db_url)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session()


def init_circuit_breaker(db_url: str = None) -> bool:
    """Initialize the circuit breaker state table.

    Returns True if initialized (or already exists).
    """
    try:
        get_db_engine(db_url)
        return True
    except Exception as e:
        logger.warning("Circuit breaker DB init failed: %s", e)
        return False


class CircuitBreaker:
    """Maximum drawdown protection engine.

    Tracks peak cumulative PnL across all pools and triggers
    a circuit breaker lock when drawdown exceeds the threshold.

    Usage:
        cb = CircuitBreaker()
        if cb.check(current_pnl, total_capital):
            # Circuit breaker triggered — halt trading
            ...
    """

    def __init__(
        self,
        threshold: float = None,
        db_url: str = None,
    ):
        """
        Args:
            threshold: Maximum drawdown as fraction (e.g., 0.20 = 20%).
            db_url: Database URL for persistence.
        """
        self.threshold = threshold or _DEFAULT_DRAWDOWN_THRESHOLD
        self.db_url = db_url
        self._state = self._load_state()

    def _load_state(self) -> Optional[CircuitBreakerState]:
        """Load current circuit breaker state from DB."""
        try:
            session = _get_session(self.db_url)
            state = session.query(CircuitBreakerState).first()
            session.close()
            return state
        except Exception as e:
            logger.warning("Failed to load circuit breaker state: %s", e)
            return None

    def _save_state(self, state: CircuitBreakerState) -> None:
        """Persist circuit breaker state to DB."""
        try:
            session = _get_session(self.db_url)
            state.updated_at = datetime.utcnow()
            session.add(state)
            session.commit()
            session.close()
        except Exception as e:
            logger.error("Failed to save circuit breaker state: %s", e)

    def check(
        self,
        current_pnl: float,
        total_capital: float,
        telegram_alert_fn=None,
    ) -> Dict:
        """Check if the circuit breaker should trigger.

        Args:
            current_pnl: Current cumulative PnL across all pools.
            total_capital: Total capital for percentage calculation.
            telegram_alert_fn: Optional callable to send Telegram alert.

        Returns:
            Dict with:
                breaker_triggered: True if breaker is now ON.
                drawdown_pct: Current drawdown from peak (%).
                peak_pnl: Peak PnL tracked.
                message: Human-readable status.
        """
        if self._state is None:
            self._state = CircuitBreakerState(
                peak_pnl=0.0,
                breaker_triggered="OFF",
            )
            self._save_state(self._state)

        peak_pnl = self._state.peak_pnl

        # Update peak PnL (never decrease)
        if current_pnl > peak_pnl:
            peak_pnl = current_pnl
            self._state.peak_pnl = peak_pnl
            self._save_state(self._state)

        # Calculate drawdown from peak
        # If peak_pnl > 0, use peak-to-trough drawdown
        # If peak_pnl <= 0, drawdown is relative to initial capital
        if peak_pnl > 0:
            drawdown = (peak_pnl - current_pnl) / peak_pnl
        elif total_capital > 0 and peak_pnl <= 0:
            # We're below initial capital — drawdown = amount lost / initial
            drawdown = max(0, (total_capital - (total_capital + current_pnl))) / total_capital
        else:
            drawdown = 0.0

        drawdown_pct = drawdown * 100
        threshold_pct = self.threshold * 100

        result = {
            "breaker_triggered": False,
            "drawdown_pct": round(drawdown_pct, 2),
            "peak_pnl": round(peak_pnl, 2),
            "current_pnl": round(current_pnl, 2),
            "threshold_pct": round(threshold_pct, 1),
            "message": f"Drawdown {drawdown_pct:.1f}% (threshold: {threshold_pct:.0f}%)",
        }

        # Check if breaker should trigger
        if current_pnl < 0 and drawdown > self.threshold:
            if self._state.breaker_triggered != "ON":
                self._state.breaker_triggered = "ON"
                now = datetime.utcnow()
                self._state.triggered_at = now
                self._state.triggered_at_ts = now.timestamp()
                self._save_state(self._state)

                result["breaker_triggered"] = True
                result["message"] = (
                    f"CIRCUIT BREAKER TRIGGERED: "
                    f"drawdown {drawdown_pct:.1f}% > "
                    f"threshold {threshold_pct:.0f}% — "
                    f"peak_pnl=${peak_pnl:.2f}, current_pnl=${current_pnl:.2f}"
                )
                logger.error(result["message"])

                # Send Telegram alert if available
                if telegram_alert_fn:
                    try:
                        telegram_alert_fn(result["message"])
                    except Exception as e:
                        logger.error("Failed to send Telegram alert: %s", e)
        elif current_pnl >= 0 and self._state.breaker_triggered == "ON":
            # Drawdown recovered — auto-reset when back in positive
            self.reset()
            result["message"] = (
                f"Circuit breaker AUTO-RESET: "
                f"drawdown {drawdown_pct:.1f}% < threshold {threshold_pct:.0f}%"
            )
            logger.info(result["message"])

        return result

    def reset(self) -> Dict:
        """Manually reset the circuit breaker to OFF state.

        Returns:
            Dict with reset status.
        """
        if self._state is None:
            return {"reset": False, "reason": "no_state"}

        if self._state.breaker_triggered == "OFF":
            return {"reset": False, "reason": "already_off"}

        now = datetime.utcnow()
        self._state.breaker_triggered = "OFF"
        self._state.reset_at = now
        self._state.reset_at_ts = now.timestamp()
        self._save_state(self._state)

        logger.warning(
            "Circuit breaker MANUAL RESET by operator. "
            "Previous drawdown: %.1f%%",
            self._state.triggered_at_ts or 0,
        )

        return {
            "reset": True,
            "reason": "manual_reset",
            "breaker_status": "OFF",
        }

    def get_status(self) -> Dict:
        """Get current circuit breaker status."""
        if self._state is None:
            return {
                "breaker_triggered": False,
                "breaker_status": "OFF",
                "peak_pnl": 0.0,
                "message": "No state initialized",
            }

        return {
            "breaker_triggered": self._state.breaker_triggered == "ON",
            "breaker_status": self._state.breaker_triggered,
            "peak_pnl": round(self._state.peak_pnl, 2),
            "triggered_at": (
                self._state.triggered_at.isoformat()
                if self._state.triggered_at else None
            ),
            "reset_at": (
                self._state.reset_at.isoformat()
                if self._state.reset_at else None
            ),
            "total_trades": self._state.total_trades,
            "win_rate": round(self._state.win_rate, 3),
            "avg_win_pct": round(self._state.avg_win_pct, 2),
            "avg_loss_pct": round(self._state.avg_loss_pct, 2),
            "threshold_pct": round(self.threshold * 100, 1),
        }

    def update_stats(
        self,
        total_trades: int,
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
    ) -> None:
        """Update circuit breaker statistics from backtest results.

        Args:
            total_trades: Number of trades in the period.
            win_rate: Win rate (0-1).
            avg_win_pct: Average win percentage.
            avg_loss_pct: Average loss percentage.
        """
        if self._state is None:
            return

        self._state.total_trades = total_trades
        self._state.win_rate = win_rate
        self._state.avg_win_pct = avg_win_pct
        self._state.avg_loss_pct = avg_loss_pct
        self._save_state(self._state)
