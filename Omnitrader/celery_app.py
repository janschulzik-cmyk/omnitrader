"""Celery application for Omnitrader.

Defines all periodic tasks that drive:
- Striker (news monitoring, position checks)
- Foundation (politician tracking, dividend rebalancing)
- Sleuth (on-chain scanning, data broker scanning, bounty cleanup)
- Hydra (pool reconciliation)
- Intelligence (learning analysis)
- Sentinel (phish scanning, credential monitoring, honeypot rotation)
"""

import os
import sys
from pathlib import Path

# Ensure project root is on the path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from celery import Celery

celery_app = Celery("omnitrader")
celery_app.config_from_object("celery_config")


# ── Striker Tasks ──────────────────────────────────────────────────

@celery_app.task(name="celery_app.news_monitor_task", bind=True)
def news_monitor_task(self):
    """Poll NewsAPI for fear-generating headlines.

    Runs every 15 minutes. Publishes FEAR_SPIKE / GREED_SPIKE events.
    """
    try:
        from src.striker.news_monitor import NewsMonitor
        monitor = NewsMonitor.load()
        monitor.run_poll()
    except Exception as e:
        celery_app.logger.error("news_monitor_task failed: %s", e)
        raise


@celery_app.task(name="celery_app.check_positions_task", bind=True)
def check_positions_task(self):
    """Check open positions, detect closures on the exchange, and update PnL.

    Runs every 30 seconds. Uses monitor_closed_trades() to detect when
    fill orders occur and record PnL in the database.
    """
    try:
        from src.striker.trade_executor import TradeExecutor
        executor = TradeExecutor.load()
        result = executor.monitor_closed_trades()
        celery_app.logger.info(
            "check_positions_task: %d trades closed, total_pnl=$%.2f",
            result.get("closed", 0), result.get("total_pnl", 0),
        )
    except Exception as e:
        celery_app.logger.error("check_positions_task failed: %s", e)
        raise


# ── Foundation Tasks ───────────────────────────────────────────────

@celery_app.task(name="celery_app.politician_tracker_task", bind=True)
def politician_tracker_task(self):
    """Fetch congressional trade disclosures and map to tradable assets.

    Runs daily at 9am UTC.
    """
    try:
        from src.foundation.politician_tracker import PoliticianTracker
        tracker = PoliticianTracker.load()
        tracker.run_daily()
    except Exception as e:
        celery_app.logger.error("politician_tracker_task failed: %s", e)
        raise


@celery_app.task(name="celery_app.dividend_rebalance_task", bind=True)
def dividend_rebalance_task(self):
    """Rebalance the dividend portfolio.

    Runs weekly (Sunday 00:00 UTC).
    """
    try:
        from src.foundation.dividend_portfolio import DividendPortfolio
        from src.foundation.rebalancer import Rebalancer

        portfolio = DividendPortfolio.load()
        rebalancer = Rebalancer.load()

        # Fetch dividend data and update holdings
        portfolio.update_dividend_data()

        # Rebalance based on current weights
        trades = rebalancer.rebalance()
        celery_app.logger.info(
            "dividend_rebalance_task completed: %d trades", len(trades),
        )
    except Exception as e:
        celery_app.logger.error("dividend_rebalance_task failed: %s", e)
        raise


# ── Sleuth Tasks ───────────────────────────────────────────────────

@celery_app.task(name="celery_app.onchain_scan_task", bind=True)
def onchain_scan_task(self):
    """Scan on-chain for anomalies, rug-pulls, mixer activity.

    Runs every hour.
    """
    try:
        from src.sleuth.onchain_scanner import OnChainScanner
        scanner = OnChainScanner.load()
        alerts = scanner.run_full_scan()
        celery_app.logger.info(
            "onchain_scan_task completed: %d alerts", len(alerts),
        )
    except Exception as e:
        celery_app.logger.error("onchain_scan_task failed: %s", e)
        raise


@celery_app.task(name="celery_app.databroker_scan_task", bind=True)
def databroker_scan_task(self):
    """Scan for data broker violations.

    Runs daily.
    """
    try:
        from src.sleuth.databroker_scanner import DataBrokerScanner
        scanner = DataBrokerScanner.load()
        violations = scanner.scan_all_brokers()
        celery_app.logger.info(
            "databroker_scan_task completed: %d violations", len(violations),
        )
    except Exception as e:
        celery_app.logger.error("databroker_scan_task failed: %s", e)
        raise


@celery_app.task(name="celery_app.bounty_cleanup_task", bind=True)
def bounty_cleanup_task(self):
    """Clean up old bounty submissions and retry failed ones.

    Runs every 12 hours.
    """
    try:
        from src.sleuth.bounty_reporter import BountyReporter
        reporter = BountyReporter.load()
        reporter.retry_failed_submissions()
    except Exception as e:
        celery_app.logger.error("bounty_cleanup_task failed: %s", e)
        raise


# ── Hydra Tasks ────────────────────────────────────────────────────

@celery_app.task(name="celery_app.reconcile_pools_task", bind=True)
def reconcile_pools_task(self):
    """Compare Hydra book balances with exchange balances.

    Runs daily. Logs discrepancies; never auto-transfers.
    """
    try:
        from src.hydra import Hydra
        hydra = Hydra.load()
        discrepancies = hydra.reconcile_with_exchange()
        celery_app.logger.info(
            "reconcile_pools_task: %d discrepancies", len(discrepancies),
        )
        if discrepancies:
            for disc in discrepancies:
                celery_app.logger.warning(
                    "Pool mismatch: %s — book $%.2f / exchange $%.2f",
                    disc["pool"], disc["book"], disc["exchange"],
                )
    except Exception as e:
        celery_app.logger.error("reconcile_pools_task failed: %s", e)
        raise


# ── Intelligence Tasks ─────────────────────────────────────────────

@celery_app.task(name="celery_app.learning_analysis_task", bind=True)
def learning_analysis_task(self):
    """Run periodic learning loop analysis.

    Runs weekly. Generates skill file updates via LLM.
    """
    try:
        from src.intelligence.learning_loop import LearningLoop
        loop = LearningLoop.load()
        result = loop.run_periodic_analysis()
        celery_app.logger.info(
            "learning_analysis_task: analyzed %d trades, generated %d updates",
            result.get("trades_analyzed", 0),
            result.get("skill_updates_generated", 0),
        )
    except Exception as e:
        celery_app.logger.error("learning_analysis_task failed: %s", e)
        raise


# ── Sentinel Tasks ─────────────────────────────────────────────────

@celery_app.task(name="celery_app.phish_domain_scan_task", bind=True)
def phish_domain_scan_task(self):
    """Scan for newly registered phishing domains.

    Runs every 12 hours.
    """
    try:
        from src.sentinel.phish_detector import PhishDetector
        detector = PhishDetector.load()
        domains = detector.scan_new_phishing_domains()
        celery_app.logger.info(
            "phish_domain_scan_task: %d new phishing domains", len(domains),
        )
    except Exception as e:
        celery_app.logger.error("phish_domain_scan_task failed: %s", e)
        raise


@celery_app.task(name="celery_app.credential_monitor_check_task", bind=True)
def credential_monitor_check_task(self):
    """Check for credential brute-force attempts.

    Runs every minute.
    """
    try:
        from src.sentinel.credential_monitor import CredentialMonitor
        monitor = CredentialMonitor.load()
        blocked = monitor.check_and_block()
        if blocked:
            celery_app.logger.warning(
                "credential_monitor_check_task: blocked %d IPs", len(blocked),
            )
    except Exception as e:
        celery_app.logger.error("credential_monitor_check_task failed: %s", e)
        raise


@celery_app.task(name="celery_app.honeypot_log_rotate_task", bind=True)
def honeypot_log_rotate_task(self):
    """Rotate honeypot event logs daily.

    Runs daily.
    """
    try:
        from src.sentinel.honeypot import Honeypot
        honeypot = Honeypot.load()
        honeypot.rotate_logs()
    except Exception as e:
        celery_app.logger.error("honeypot_log_rotate_task failed: %s", e)
        raise
