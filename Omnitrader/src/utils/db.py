"""Database models and utilities for Omnitrader.

SQLAlchemy-based ORM with SQLite for development and PostgreSQL for production.
All operations run in transactions for safety.
"""

import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .logging_config import get_logger

logger = get_logger("db")

# Database URL from environment or config
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///data/omnitrader.db",
)

Base = declarative_base()


# ============================================================
# Enums
# ============================================================
class PoolName(str, Enum):
    MOAT = "moat"
    STRIKER = "striker"
    FOUNDATION = "foundation"


class AlertSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"
    TARGET_HIT = "TARGET_HIT"
    PARTIAL = "PARTIAL"


class SubmissionStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ============================================================
# Sentinel & Mesh Models
# ============================================================

class HoneypotEvent(Base):
    """Tracks access attempts to honeypot endpoints."""

    __tablename__ = "honeypot_events"

    id = Column(Integer, primary_key=True)
    ip_address = Column(String(45), nullable=False)
    user_agent = Column(Text, nullable=True)
    method = Column(String(8), nullable=False)
    path = Column(String(256), nullable=False)
    route = Column(String(64), nullable=False)  # which honeypot route
    headers_json = Column(Text, nullable=True)
    body_json = Column(Text, nullable=True)
    fake_key_used = Column(String(128), nullable=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "method": self.method,
            "path": self.path,
            "route": self.route,
            "fake_key_used": self.fake_key_used,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class MeshNodeRecord(Base):
    """Tracks mesh network nodes."""

    __tablename__ = "mesh_nodes"

    id = Column(Integer, primary_key=True)
    node_id = Column(String(32), unique=True, nullable=False)
    ip_address = Column(String(45), nullable=False)
    port = Column(Integer, nullable=False)
    cluster = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="ACTIVE")
    registered_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "ip_address": self.ip_address,
            "port": self.port,
            "cluster": self.cluster,
            "status": self.status,
            "registered_at": self.registered_at.isoformat() if self.registered_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


class SystemSetting(Base):
    """Key-value system settings (e.g., striker_paused)."""

    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)
    value = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PoolBalance(Base):
    """Tracks virtual capital pool balances."""

    __tablename__ = "pool_balances"

    id = Column(Integer, primary_key=True)
    pool_name = Column(String(32), unique=True, nullable=False)
    balance = Column(Float, nullable=False, default=0.0)
    total_deposited = Column(Float, nullable=False, default=0.0)
    total_withdrawn = Column(Float, nullable=False, default=0.0)
    total_profit = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pool_name": self.pool_name,
            "balance": self.balance,
            "total_deposited": self.total_deposited,
            "total_withdrawn": self.total_withdrawn,
            "total_profit": self.total_profit,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class WatchdogTokenLedger(Base):
    """Tracks WATCHDOG token rewards for reporters and DAO."""

    __tablename__ = "watchdog_token_ledger"

    id = Column(Integer, primary_key=True)
    reporter_id = Column(String(64), nullable=True)  # Telegram user ID or wallet address
    reward_type = Column(String(32), nullable=False)  # bounty, bounty_reporter, dao_treasury, burned
    amount = Column(Float, nullable=False)
    related_bounty_id = Column(Integer, ForeignKey("bounty_submissions.id"), nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "reporter_id": self.reporter_id,
            "reward_type": self.reward_type,
            "amount": self.amount,
            "related_bounty_id": self.related_bounty_id,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Trade(Base):
    """Tracks all trades executed by the Striker module."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    pair = Column(String(16), nullable=False)
    side = Column(String(4), nullable=False)  # BUY or SELL
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    risk_amount = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)  # PnL as a percentage
    outcome = Column(String(8), nullable=True)  # WIN, LOSS, or null for open trades
    is_closed = Column(Boolean, nullable=False, default=False)
    status = Column(String(16), nullable=False, default="PENDING")
    trigger_fear_score = Column(Float, nullable=True)
    trigger_headline = Column(Text, nullable=True)
    volume_anomaly = Column(Boolean, nullable=False, default=False)
    candle_pattern = Column(String(32), nullable=True)
    exchange_order_id = Column(String(64), nullable=True)
    tag = Column(String(32), nullable=True)  # e.g., "foundation", "striker"
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "pair": self.pair,
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "risk_amount": self.risk_amount,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "outcome": self.outcome,
            "is_closed": self.is_closed,
            "status": self.status,
            "trigger_fear_score": self.trigger_fear_score,
            "trigger_headline": self.trigger_headline,
            "volume_anomaly": self.volume_anomaly,
            "candle_pattern": self.candle_pattern,
            "exchange_order_id": self.exchange_order_id,
            "tag": self.tag,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TradeReflection(Base):
    """Records post-trade reflections for the learning loop."""

    __tablename__ = "trade_reflections"

    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, nullable=True)
    pair = Column(String(16), nullable=True)
    side = Column(String(4), nullable=True)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    trigger_fear_score = Column(Float, nullable=True)
    trigger_headline = Column(Text, nullable=True)
    candle_pattern = Column(String(32), nullable=True)
    volume_anomaly = Column(Boolean, nullable=False, default=False)
    outcome = Column(String(16), nullable=True)  # WIN, LOSS, BREAKEVEN
    notes = Column(Text, nullable=True)  # LLM-generated reflection notes
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "trade_id": self.trade_id,
            "pair": self.pair,
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "outcome": self.outcome,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class OnChainAlert(Base):
    """Tracks on-chain investigation alerts from the Sleuth module."""

    __tablename__ = "onchain_alerts"

    id = Column(Integer, primary_key=True)
    alert_type = Column(String(64), nullable=False)  # e.g., "rugpull_detected", "mixer_deposit"
    network = Column(String(32), nullable=False)  # ethereum, arbitrum, bsc
    severity = Column(String(16), nullable=False, default="MEDIUM")
    target_address = Column(String(64), nullable=True)
    wallet_addresses = Column(Text, nullable=True)  # JSON array of addresses
    tx_hashes = Column(Text, nullable=True)  # JSON array of tx hashes
    evidence = Column(Text, nullable=True)  # JSON evidence object
    value_usd = Column(Float, nullable=True)
    submitted_as_bounty = Column(Boolean, nullable=False, default=False)
    bounty_submission_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "alert_type": self.alert_type,
            "network": self.network,
            "severity": self.severity,
            "target_address": self.target_address,
            "wallet_addresses": self.wallet_addresses,
            "tx_hashes": self.tx_hashes,
            "evidence": self.evidence,
            "value_usd": self.value_usd,
            "submitted_as_bounty": self.submitted_as_bounty,
            "bounty_submission_id": self.bounty_submission_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DataBrokerAlert(Base):
    """Tracks data broker violation alerts."""

    __tablename__ = "databroker_alerts"

    id = Column(Integer, primary_key=True)
    broker_name = Column(String(64), nullable=False)
    violation_type = Column(String(64), nullable=False)  # e.g., "ccpa_violation", "gdpr_violation"
    severity = Column(String(16), nullable=False, default="MEDIUM")
    evidence = Column(Text, nullable=True)  # JSON evidence object
    opt_out_request_id = Column(String(64), nullable=True)
    opt_out_date = Column(DateTime, nullable=True)
    violation_detected = Column(DateTime, nullable=False, default=datetime.utcnow)
    submitted_to_ftc = Column(Boolean, nullable=False, default=False)
    submitted_to_ag = Column(Boolean, nullable=False, default=False)
    class_action_lead = Column(Boolean, nullable=False, default=False)
    is_verified = Column(Boolean, nullable=False, default=False)
    broker_website = Column(String(512), nullable=True)
    description = Column(Text, nullable=True)
    statute = Column(String(128), nullable=True)
    last_scanned = Column(DateTime, nullable=True)
    violations_found = Column(Integer, nullable=True, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "broker_name": self.broker_name,
            "violation_type": self.violation_type,
            "severity": self.severity,
            "evidence": self.evidence,
            "opt_out_request_id": self.opt_out_request_id,
            "opt_out_date": self.opt_out_date.isoformat() if self.opt_out_date else None,
            "violation_detected": self.violation_detected.isoformat() if self.violation_detected else None,
            "submitted_to_ftc": self.submitted_to_ftc,
            "submitted_to_ag": self.submitted_to_ag,
            "class_action_lead": self.class_action_lead,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_scanned": self.last_scanned.isoformat() if self.last_scanned else None,
            "violations_found": self.violations_found,
        }


class BountySubmission(Base):
    """Tracks bounty report submissions."""

    __tablename__ = "bounty_submissions"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(64), nullable=False)
    network = Column(String(32), nullable=True)
    target_address = Column(String(64), nullable=True)
    evidence_summary = Column(Text, nullable=True)
    report_path = Column(String(512), nullable=True)
    submitted_to = Column(String(64), nullable=False)  # program key
    submission_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    status = Column(String(16), nullable=False, default="PENDING")
    tx_hashes = Column(Text, nullable=True)  # JSON array
    value_at_risk = Column(Float, nullable=True)
    report_id = Column(String(64), nullable=True)
    reporter_id = Column(String(64), nullable=True)
    watchdog_reward_amount = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "network": self.network,
            "target_address": self.target_address,
            "evidence_summary": self.evidence_summary,
            "report_path": self.report_path,
            "submitted_to": self.submitted_to,
            "submission_date": self.submission_date.isoformat() if self.submission_date else None,
            "status": self.status,
            "tx_hashes": self.tx_hashes,
            "value_at_risk": self.value_at_risk,
            "report_id": self.report_id,
            "reporter_id": self.reporter_id,
            "watchdog_reward_amount": self.watchdog_reward_amount,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PoliticianTrade(Base):
    """Tracks congressional trade signals."""

    __tablename__ = "politician_trades"

    id = Column(Integer, primary_key=True)
    politician_name = Column(String(128), nullable=False)
    stock_ticker = Column(String(16), nullable=False)
    company_name = Column(String(128), nullable=True)
    transaction_type = Column(String(8), nullable=False)  # BUY, SELL
    transaction_value = Column(Float, nullable=False)
    filing_date = Column(DateTime, nullable=False)
    mapped_token = Column(String(32), nullable=True)
    execution_status = Column(String(16), nullable=True)  # EXECUTED, SKIPPED
    forward_return_pct = Column(Float, nullable=True)
    politician_confidence = Column(Float, nullable=True)  # 0-100 based on historical accuracy
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "politician_name": self.politician_name,
            "stock_ticker": self.stock_ticker,
            "company_name": self.company_name,
            "transaction_type": self.transaction_type,
            "transaction_value": self.transaction_value,
            "filing_date": self.filing_date.isoformat() if self.filing_date else None,
            "mapped_token": self.mapped_token,
            "execution_status": self.execution_status,
            "forward_return_pct": self.forward_return_pct,
            "politician_confidence": self.politician_confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DividendHolding(Base):
    """Tracks dividend portfolio holdings."""

    __tablename__ = "dividend_holdings"

    id = Column(Integer, primary_key=True)
    ticker = Column(String(16), nullable=False, unique=True)
    shares = Column(Float, nullable=False, default=0.0)
    avg_cost = Column(Float, nullable=False, default=0.0)
    current_price = Column(Float, nullable=True)
    target_weight = Column(Float, nullable=False)
    sector = Column(String(32), nullable=True)
    dividend_yield = Column(Float, nullable=True)
    payout_ratio = Column(Float, nullable=True)
    flagged_for_review = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "shares": self.shares,
            "avg_cost": self.avg_cost,
            "current_price": self.current_price,
            "target_weight": self.target_weight,
            "sector": self.sector,
            "dividend_yield": self.dividend_yield,
            "payout_ratio": self.payout_ratio,
            "flagged_for_review": self.flagged_for_review,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SystemEvent(Base):
    """Logs system events for audit trail."""

    __tablename__ = "system_events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(64), nullable=False)
    event_id = Column(String(32), nullable=True)  # Unique event ID from LogEvent
    module = Column(String(64), nullable=True)
    message = Column(Text, nullable=False)
    details = Column(Text, nullable=True)  # JSON details
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "module": self.module,
            "message": self.message,
            "details": self.details,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class OptOutRecord(Base):
    """Tracks opt-out requests submitted to data brokers."""

    __tablename__ = "opt_out_records"

    id = Column(Integer, primary_key=True)
    broker_name = Column(String(64), nullable=False)
    method = Column(String(32), nullable=False)  # browser, email, api
    request_id = Column(String(16), nullable=True)
    status = Column(String(16), nullable=False, default="PENDING")
    response_data = Column(Text, nullable=True)  # JSON response from broker
    last_attempt = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "broker_name": self.broker_name,
            "method": self.method,
            "request_id": self.request_id,
            "status": self.status,
            "last_attempt": self.last_attempt.isoformat() if self.last_attempt else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class VPNNodeRecord(Base):
    """Tracks VPN/WireGuard nodes."""

    __tablename__ = "vpn_nodes"

    id = Column(Integer, primary_key=True)
    address = Column(String, unique=True)
    port = Column(Integer)
    status = Column(String, default="unknown")


class TokenRewardRecord(Base):
    """Tracks WATCHDOG token reward distributions to node operators."""

    __tablename__ = "token_rewards"

    id = Column(Integer, primary_key=True)
    node_id = Column(String(64), nullable=True)
    recipient = Column(String(128), nullable=True)  # wallet address or node ID
    reward_amount = Column(Float, nullable=False, default=0.0)
    uptime_hours = Column(Float, nullable=True)
    bandwidth_gb = Column(Float, nullable=True)
    reason = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "recipient": self.recipient,
            "reward_amount": self.reward_amount,
            "uptime_hours": self.uptime_hours,
            "bandwidth_gb": self.bandwidth_gb,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DAOTransaction(Base):
    """Tracks DAO token transactions (minting, burning, transfers)."""

    __tablename__ = "dao_transactions"

    id = Column(Integer, primary_key=True)
    token_amount = Column(Float, nullable=False)
    recipient = Column(String(128), nullable=True)
    transaction_type = Column(String(16), nullable=False)  # MINT, BURN, TRANSFER
    reason = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="PENDING")
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "token_amount": self.token_amount,
            "recipient": self.recipient,
            "transaction_type": self.transaction_type,
            "reason": self.reason,
            "status": self.status,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# Database Initialization
# ============================================================
_engine = None
_session_factory = None


def init_db(db_url: str = None) -> None:
    """Initialize the database engine and create tables.

    Args:
        db_url: Database URL. Defaults to DATABASE_URL from env.
    """
    global _engine, _session_factory
    db_url = db_url or DATABASE_URL

    # Enable WAL mode for SQLite
    if db_url.startswith("sqlite"):
        _engine = create_engine(db_url, echo=False)
        event.listen(_engine, "connect", _set_sqlite_pragma)
    else:
        _engine = create_engine(db_url, echo=False, pool_size=10, max_overflow=20)

    Base.metadata.create_all(_engine)
    _session_factory = sessionmaker(bind=_engine)

    logger.info("Database initialized: %s", db_url)


def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode and foreign keys for SQLite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


def get_session():
    """Get a new database session.

    Returns:
        SQLAlchemy session.
    """
    global _session_factory
    if _session_factory is None:
        init_db()
    return _session_factory()


def log_system_event(
    event_type: str,
    message: str,
    module: str = None,
    details: Dict[str, Any] = None,
    event_id: str = None,
) -> SystemEvent:
    """Log a system event to the database.

    Args:
        event_type: Type of event.
        message: Human-readable message.
        module: Module that generated the event.
        details: Additional context as dict.
        event_id: Unique event ID.

    Returns:
        The stored SystemEvent record.
    """
    session = get_session()
    try:
        import json

        event = SystemEvent(
            event_type=event_type,
            event_id=event_id,
            module=module,
            message=message,
            details=json.dumps(details) if details else None,
        )
        session.add(event)
        session.commit()
        return event
    except Exception as e:
        session.rollback()
        logger.error("Failed to log system event: %s", e)
        raise
    finally:
        session.close()


def create_initial_pools(
    total_capital: float,
    moat_ratio: float = 0.10,
    foundation_ratio: float = 0.20,
    striker_ratio: float = 0.70,
) -> None:
    """Create initial pool balances if they don't exist.

    Args:
        total_capital: Total capital to allocate.
        moat_ratio: Fraction for Moat pool.
        foundation_ratio: Fraction for Foundation pool.
        striker_ratio: Fraction for Striker pool.
    """
    session = get_session()
    try:
        for pool_name in ("moat", "striker", "foundation", "dao_treasury"):
            existing = session.query(PoolBalance).filter_by(pool_name=pool_name).first()
            if existing is None:
                if pool_name == "dao_treasury":
                    # DAO treasury starts at 0
                    balance = 0.0
                    ratio = 0.0
                else:
                    ratio_map = {
                        "moat": moat_ratio,
                        "striker": striker_ratio,
                        "foundation": foundation_ratio,
                    }
                    ratio = ratio_map.get(pool_name, 0.0)
                    balance = total_capital * ratio

                pool = PoolBalance(
                    pool_name=pool_name,
                    balance=balance,
                    total_deposited=balance,
                )
                session.add(pool)
                logger.info(
                    "Created initial pool: %s = %.2f (ratio=%.2f)",
                    pool_name, balance, ratio,
                )

        session.commit()
    except Exception as e:
        session.rollback()
        logger.error("Failed to create initial pools: %s", e)
        raise
    finally:
        session.close()


# ============================================================
# Query Helpers
# ============================================================
def get_open_trades() -> List[Trade]:
    """Get all open (non-closed) trades.

    Returns:
        List of Trade records with status PENDING or FILLED.
    """
    session = get_session()
    try:
        return session.query(Trade).filter(
            Trade.status.in_(["PENDING", "FILLED", "PARTIAL"])
        ).all()
    finally:
        session.close()


def get_recent_trades(limit: int = 20) -> List[Trade]:
    """Get recent trades ordered by date.

    Args:
        limit: Maximum number of trades to return.

    Returns:
        List of Trade records.
    """
    session = get_session()
    try:
        return (
            session.query(Trade)
            .order_by(Trade.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        session.close()


def get_pending_bounties() -> List[BountySubmission]:
    """Get bounty submissions that haven't been sent yet.

    Returns:
        List of BountySubmission records with status PENDING.
    """
    session = get_session()
    try:
        return (
            session.query(BountySubmission)
            .filter_by(status="PENDING")
            .order_by(BountySubmission.created_at.asc())
            .all()
        )
    finally:
        session.close()
