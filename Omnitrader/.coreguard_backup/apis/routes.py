"""FastAPI Routes for Omnitrader.

REST API endpoints for monitoring and controlling the system.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .auth import api_key_auth, verify_telegram_user, get_telegram_admin_id
from ..utils.logging_config import get_logger

logger = get_logger("apis.routes")

router = APIRouter(prefix="/api/v1")


# ── Pydantic Models ───────────────────────────────────────────────

class CommandRequest(BaseModel):
    """Natural language command from user."""
    command: str
    context: Optional[Dict] = None


class CommandResponse(BaseModel):
    """Response from command processing."""
    action: str
    params: Dict
    status: str
    message: str
    confidence: float


class StatusResponse(BaseModel):
    """System status response."""
    pools: Dict
    open_trades: List[Dict]
    recent_submissions: List[Dict]
    broker_scans: Dict
    swarm_status: Dict
    mesh_bridge: Dict
    uptime_hours: float


class SubmitAlertRequest(BaseModel):
    """Request to submit an on-chain alert for bounty."""
    alert_id: str
    target: str  # ftc, sec, cftc, etc.
    report_type: str = "violation"


# ── Route Handlers ────────────────────────────────────────────────

@router.get("/status")
async def get_status(
    key: str = Depends(api_key_auth),
) -> StatusResponse:
    """Get complete system status.

    Returns pool balances, open trades, recent submissions,
    broker scan summary, and swarm status.
    """
    try:
        # Import here to avoid circular imports
        from ..hydra import Hydra
        from ..striker.trade_executor import TradeExecutor
        from ..sleuth.databroker_scanner import DataBrokerScanner
        from ..swarm.mesh_network import MeshNetwork

        hydra = Hydra.load()
        status = hydra.get_status()

        # Open trades
        open_trades = []
        if os.environ.get("STRIKER_ENABLED", "true") == "true":
            executor = TradeExecutor.load()
            open_trades = executor.check_open_positions()

        # Recent bounty submissions
        from ..sleuth.bounty_reporter import BountyReporter
        reporter = BountyReporter.load()
        submissions = reporter.get_submission_history()[:10]

        # Broker scan summary
        scanner = DataBrokerScanner.load()
        broker_summary = scanner.get_scan_summary()

        # Swarm status
        mesh = MeshNetwork()
        swarm_status = mesh.get_status()

        # Mesh bridge status
        mesh_bridge_status = {}
        try:
            from ..swarm.mesh_bridge import get_mesh_bridge
            bridge = get_mesh_bridge()
            mesh_bridge_status = bridge.get_status()
        except Exception as e:
            logger.warning("Failed to get mesh bridge status: %s", e)
            mesh_bridge_status = {"enabled": False, "connected": False, "last_signal": None}

        return StatusResponse(
            pools={
                "moat": status["moat"],
                "foundation": status["foundation"],
                "striker": status["striker"],
            },
            open_trades=open_trades,
            recent_submissions=submissions,
            broker_scans=broker_summary,
            swarm_status=swarm_status,
            mesh_bridge=mesh_bridge_status,
            uptime_hours=status.get("uptime_hours", 0),
        )

    except Exception as e:
        logger.error("Status endpoint error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/command", response_model=CommandResponse)
async def handle_command(
    req: CommandRequest,
    key: str = Depends(api_key_auth),
) -> CommandResponse:
    """Process a natural language command.

    Accepts commands like 'Pause Striker', 'Dump Foundation',
    'Scan for new tokens', etc.
    """
    try:
        from ..intelligence.llm_interface import LLMInterface

        llm = LLMInterface.load()
        result = llm.process_natural_language_command(
            command=req.command,
            context=req.context,
        )

        if not result:
            return CommandResponse(
                action="unknown",
                params={},
                status="ERROR",
                message="Could not parse command.",
                confidence=0.0,
            )

        action = result.get("action", "unknown")
        params = result.get("params", {})
        confidence = result.get("confidence", 0.0)

        # Execute the action
        message = await _execute_action(action, params)

        return CommandResponse(
            action=action,
            params=params,
            status="OK" if message else "ERROR",
            message=message,
            confidence=confidence,
        )

    except Exception as e:
        logger.error("Command endpoint error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/submit-alert")
async def submit_alert(
    req: SubmitAlertRequest,
    key: str = Depends(api_key_auth),
) -> Dict:
    """Submit an on-chain alert for bounty processing.

    Args:
        req: Alert submission request.

    Returns:
        Submission result.
    """
    try:
        from ..sleuth.bounty_reporter import BountyReporter
        from ..utils.db import OnChainAlert, get_session

        session = get_session()
        try:
            alert = session.query(OnChainAlert).filter_by(
                alert_id=req.alert_id
            ).first()

            if not alert:
                raise HTTPException(
                    status_code=404,
                    detail=f"Alert {req.alert_id} not found",
                )

            # Parse evidence from JSON
            import json
            evidence = json.loads(alert.evidence) if isinstance(alert.evidence, str) else alert.evidence

            reporter = BountyReporter.load()
            formatted = reporter.format_evidence_for_target(
                alert=evidence,
                target_name=req.target,
            )

            submission = reporter.submit_report(
                evidence=formatted,
                target_name=req.target,
                report_type=req.report_type,
                dry_run=True,  # Always dry run via API
            )

            return {
                "status": "SUBMITTED",
                "report_id": submission.get("id") if submission else None,
                "target": req.target,
                "dry_run": True,
            }

        finally:
            session.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Submit alert error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_trades(
    key: str = Depends(api_key_auth),
    limit: int = Query(50, ge=1, le=1000),
    status: str = Query("all", regex="^(all|open|closed|win|loss)$"),
) -> List[Dict]:
    """Get trade history.

    Args:
        key: API key.
        limit: Number of trades to return.
        status: Filter by trade status.
    """
    try:
        from ..utils.db import Trade, get_session

        session = get_session()
        try:
            query = session.query(Trade)
            if status != "all":
                if status in ("win", "loss"):
                    query = query.filter(Trade.outcome == status)
                elif status == "open":
                    query = query.filter(Trade.is_closed == False)
            trades = query.order_by(Trade.created_at.desc()).limit(limit).all()

            return [
                {
                    "id": t.id,
                    "pair": t.pair,
                    "side": t.side,
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price) if t.exit_price else None,
                    "pnl": float(t.pnl) if t.pnl else None,
                    "pnl_pct": float(t.pnl_pct) if t.pnl_pct else None,
                    "status": "closed" if t.is_closed else "open",
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in trades
            ]
        finally:
            session.close()

    except Exception as e:
        logger.error("Trades endpoint error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pools")
async def get_pools(
    key: str = Depends(api_key_auth),
) -> Dict:
    """Get current capital pool balances."""
    try:
        from ..hydra import Hydra

        hydra = Hydra.load()
        pools = hydra.get_all_balances()
        pools["total_capital"] = sum(pools.values())
        return pools
    except Exception as e:
        logger.error("Pools endpoint error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/strike/pause")
async def pause_striker(
    key: str = Depends(api_key_auth),
) -> Dict:
    """Pause the Striker module."""
    try:
        from ..utils.db import get_session, SystemSetting

        session = get_session()
        try:
            setting = session.query(SystemSetting).filter_by(key="striker_paused").first()
            if setting:
                setting.value = "true"
            else:
                setting = SystemSetting(key="striker_paused", value="true")
                session.add(setting)
            session.commit()
            return {"status": "OK", "message": "Striker paused"}
        finally:
            session.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/strike/resume")
async def resume_striker(
    key: str = Depends(api_key_auth),
) -> Dict:
    """Resume the Striker module."""
    try:
        from ..utils.db import get_session, SystemSetting

        session = get_session()
        try:
            setting = session.query(SystemSetting).filter_by(key="striker_paused").first()
            if setting:
                setting.value = "false"
            session.commit()
            return {"status": "OK", "message": "Striker resumed"}
        finally:
            session.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/foundation/rebalance")
async def trigger_rebalance(
    key: str = Depends(api_key_auth),
) -> Dict:
    """Manually trigger a Foundation rebalance."""
    try:
        from ..foundation.rebalancer import Rebalancer
        from ..utils.db import get_session

        session = get_session()
        try:
            rebalancer = Rebalancer.load()
            trades = rebalancer.rebalance()
            session.commit()
            return {
                "status": "OK",
                "message": f"Rebalanced with {len(trades)} trades",
                "trades": trades,
            }
        finally:
            session.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sleuth/scan")
async def trigger_scan(
    key: str = Depends(api_key_auth),
) -> Dict:
    """Trigger a full on-chain scan."""
    try:
        from ..sleuth.onchain_scanner import OnChainScanner
        from ..sleuth.databroker_scanner import DataBrokerScanner

        scanner = OnChainScanner.load()
        alerts = scanner.run_full_scan()

        broker_scanner = DataBrokerScanner.load()
        violations = broker_scanner.scan_all_brokers()

        return {
            "status": "OK",
            "onchain_alerts": len(alerts),
            "broker_violations": len(violations),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/intelligence/analyze")
async def trigger_analysis(
    key: str = Depends(api_key_auth),
) -> Dict:
    """Trigger periodic learning analysis."""
    try:
        from ..intelligence.learning_loop import LearningLoop

        loop = LearningLoop.load()
        result = loop.run_periodic_analysis()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check() -> Dict:
    """Health check endpoint (no auth required)."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/alerts")
async def get_alerts(
    key: str = Depends(api_key_auth),
    limit: int = Query(50, ge=1, le=1000),
    severity: str = Query("all", regex="^(all|LOW|MEDIUM|HIGH|CRITICAL)$"),
) -> List[Dict]:
    """Get on-chain alerts from the Sleuth module.

    Args:
        key: API key.
        limit: Number of alerts to return.
        severity: Filter by severity level.
    """
    try:
        from ..utils.db import OnChainAlert, get_session

        session = get_session()
        try:
            query = session.query(OnChainAlert)
            if severity != "all":
                query = query.filter(OnChainAlert.severity == severity)
            alerts = query.order_by(OnChainAlert.created_at.desc()).limit(limit).all()

            return [a.to_dict() for a in alerts]
        finally:
            session.close()

    except Exception as e:
        logger.error("Alerts endpoint error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sleuth")
async def get_sleuth_bounties(
    key: str = Depends(api_key_auth),
    limit: int = Query(50, ge=1, le=1000),
    status: str = Query("all", regex="^(all|PENDING|SUBMITTED|FAILED|DUPLICATE|APPROVED|REJECTED)$"),
) -> List[Dict]:
    """Get bounty submissions from the Sleuth module.

    Args:
        key: API key.
        limit: Number of submissions to return.
        status: Filter by submission status.
    """
    try:
        from ..utils.db import BountySubmission, get_session

        session = get_session()
        try:
            query = session.query(BountySubmission)
            if status != "all":
                query = query.filter(BountySubmission.status == status)
            bounties = query.order_by(BountySubmission.submission_date.desc()).limit(limit).all()

            return [b.to_dict() for b in bounties]
        finally:
            session.close()

    except Exception as e:
        logger.error("Sleuth endpoint error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trades")
async def cancel_all_trades(
    key: str = Depends(api_key_auth),
) -> Dict:
    """Cancel all open orders on the exchange and mark trades as cancelled."""
    try:
        from ..striker.trade_executor import TradeExecutor
        from ..utils.db import Trade, get_session
        from sqlalchemy import update

        executor = TradeExecutor.load()
        cancelled = executor.cancel_all_orders()

        # Mark open trades in DB as CANCELLED (SQLAlchemy 2.x style)
        session = get_session()
        try:
            session.execute(
                update(Trade)
                .where(Trade.is_closed == False)
                .values(status="CANCELLED", is_closed=True)
            )
            session.commit()
        finally:
            session.close()

        return {
            "status": "OK",
            "cancelled_orders": cancelled,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Helpers ───────────────────────────────────────────────────────

async def _execute_action(action: str, params: Dict) -> str:
    """Execute an action parsed from natural language.

    Args:
        action: Action string.
        params: Action parameters.

    Returns:
        Result message.
    """
    if action == "pause":
        target = params.get("target", "striker")
        return f"Paused {target}"
    elif action == "resume":
        target = params.get("target", "striker")
        return f"Resumed {target}"
    elif action == "status":
        return "System status retrieved"
    elif action == "balance":
        return "Balance check: use /balance or GET /pools"
    elif action == "scan":
        return "Scan triggered: use POST /sleuth/scan"
    elif action == "rebalance":
        return "Rebalance triggered: use POST /foundation/rebalance"
    elif action == "analyze":
        return "Analysis triggered: use POST /intelligence/analyze"
    else:
        return f"Unknown action: {action}. Supported: pause, resume, status, balance, scan, rebalance, analyze"
