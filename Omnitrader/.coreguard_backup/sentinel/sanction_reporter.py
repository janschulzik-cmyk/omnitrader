"""Sanction reporter for Omnitrader Sentinel.

Formats detected identity attacks (phishing, credential
stuffing) into reports for platforms such as IC3, APWG,
registrar abuse contacts, and exchanges.

Auto-Reporting Module: Checks for unsubmitted HoneypotEvents and OnChainAlerts
with severity >= HIGH every 30 minutes and auto-submits them via SMTP pipeline.
"""

import os
import json
import smtplib
import ssl
import hashlib
import threading
import time
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, List, Optional
from pathlib import Path

import requests

from ..utils.logging_config import get_logger
from ..utils.db import (
    OnChainAlert,
    BountySubmission,
    HoneypotEvent,
    get_session,
)

logger = get_logger("sentinel.sanction_reporter")

# ── Auto-reporting configuration ─────────────────────────────────────

AUTO_REPORT_INTERVAL_SEC = int(
    os.environ.get("SENTINEL_AUTO_REPORT_INTERVAL", "1800")  # 30 minutes
)
AUTO_REPORT_MIN_SEVERITY = os.environ.get(
    "SENTINEL_AUTO_REPORT_MIN_SEVERITY", "HIGH"
)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


class SanctionReporterConfig:
    """Configuration for the sanction reporter."""

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = "omnitrader@protected.local"
    ic3_email: str = "ic3@ic3.gov"
    apwg_email: str = "report@apwg.org"
    registrar_abuse_emails: Dict[str, str] = {
        "godaddy": "abuse@godaddy.com",
        "namecheap": "abuse@namecheap.com",
        "cloudflare": "abuse@cloudflare.com",
    }
    report_output_dir: str = "/var/log/omnitrader/reports"


class SanctionReporter:
    """Formats and submits abuse reports to external authorities."""

    _instance: Optional["SanctionReporter"] = None

    def __init__(self, config: SanctionReporterConfig = None):
        self.config = config or SanctionReporterConfig()
        self.report_dir = Path(self.config.report_output_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls) -> "SanctionReporter":
        if cls._instance is None:
            cls._instance = SanctionReporter()
        return cls._instance

    def format_report(
        self,
        alert: OnChainAlert,
        report_type: str = "phishing",
        additional_context: Dict = None,
    ) -> Dict:
        """Format an alert into a structured abuse report.

        Args:
            alert: The OnChainAlert to report.
            report_type: Type of abuse (phishing, credential_stuffing, etc.).
            additional_context: Extra context to include.

        Returns:
            Structured report dict.
        """
        report = {
            "report_id": self._generate_report_id(alert),
            "type": report_type,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "severity": alert.severity,
            "summary": alert.alert_type,
            "description": self._generate_narrative(alert, report_type),
            "evidence": {
                "target_address": alert.target_address,
                "tx_hashes": json.loads(alert.tx_hashes) if alert.tx_hashes else [],
                "wallet_addresses": json.loads(alert.wallet_addresses) if alert.wallet_addresses else [],
                "evidence_detail": json.loads(alert.evidence) if alert.evidence else {},
            },
            "additional_context": additional_context or {},
        }
        return report

    def _generate_report_id(self, alert: OnChainAlert) -> str:
        """Generate a unique report ID.

        Args:
            alert: Source alert.

        Returns:
            Report ID string.
        """
        raw = f"{alert.id}_{alert.alert_type}_{alert.created_at.isoformat()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def _generate_narrative(self, alert: OnChainAlert, report_type: str) -> str:
        """Generate a human-readable narrative for the report.

        Args:
            alert: The alert.
            report_type: Type of abuse.

        Returns:
            Narrative text.
        """
        base = (
            f"This report documents a {report_type} attempt "
            f"targeting infrastructure associated with Omnitrader.\n\n"
        )

        evidence_detail = {}
        if alert.evidence:
            try:
                evidence_detail = json.loads(alert.evidence)
            except (json.JSONDecodeError, TypeError):
                evidence_detail = {}

        details = {
            "phishing": (
                "A phishing domain was detected that closely mimics a legitimate "
                "service. The domain was newly registered and exhibits patterns "
                "consistent with credential harvesting campaigns.\n\n"
                "The target domain was: {}\n"
                "Registered: {}\n"
                "Registrar: {}\n"
                "Country: {}".format(
                    alert.target_address,
                    evidence_detail.get("created_date", "N/A"),
                    evidence_detail.get("registrar", "N/A"),
                    evidence_detail.get("registrar_country", "N/A"),
                )
            ),
            "credential_stuffing": (
                "Repeated failed authentication attempts were detected from "
                "a single IP address, indicating a brute-force or credential "
                "stuffing attack.\n\n"
                "The attacking IP was: {}\n"
                "Failed attempts recorded: {}".format(
                    alert.target_address,
                    evidence_detail.get("attempt_count", "N/A"),
                )
            ),
            "general": (
                "An alert was generated by the Omnitrader Sentinel module "
                "indicating suspicious activity: {}\n\n"
                "Severity: {}\n"
                "Network: {}".format(
                    alert.alert_type,
                    alert.severity,
                    alert.network,
                )
            ),
        }

        return base + details.get(report_type, details["general"])

    def send_report(
        self,
        report: Dict,
        target: str = "ic3",
        dry_run: bool = False,
    ) -> Dict:
        """Send a formatted report to the specified authority.

        Args:
            report: Formatted report dict.
            target: Target authority (ic3, apwg, registrar, etc.).
            dry_run: If True, only log without sending.

        Returns:
            Submission result dict.
        """
        if dry_run:
            logger.info("DRY RUN: Would send report %s to %s", report["report_id"], target)
            return {
                "status": "DRY_RUN",
                "report_id": report["report_id"],
                "target": target,
            }

        email = self._resolve_email(target)
        if not email:
            logger.error("No email configured for target: %s", target)
            return {"status": "ERROR", "message": f"No email for {target}"}

        # Build email
        msg = MIMEMultipart()
        msg["From"] = self.config.from_address
        msg["To"] = email
        msg["Subject"] = f"Abuse Report: {report['type']} - {report['report_id']}"

        body = (
            f"OMNITRADER SENTINEL REPORT\n"
            f"Report ID: {report['report_id']}\n"
            f"Generated: {report['generated_at']}\n\n"
            f"{report['description']}\n"
        )

        msg.attach(MIMEText(body, "plain"))

        # Attach report text file
        txt_path = self._save_report_txt(report)
        if txt_path and os.path.exists(txt_path):
            try:
                with open(txt_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f'attachment; filename="report_{report["report_id"]}.txt"',
                    )
                    msg.attach(part)
            except Exception as e:
                logger.warning("Failed to attach report: %s", e)

        # Send email
        try:
            context = ssl.create_default_context()
            server = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port)
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(
                self.config.smtp_user,
                self.config.smtp_password,
            )
            server.sendmail(self.config.from_address, [email], msg.as_string())
            server.quit()

            logger.info("Report %s sent to %s via email", report["report_id"], email)

            # Log to DB
            self._log_submission(report, email)

            return {
                "status": "SENT",
                "report_id": report["report_id"],
                "target": email,
            }

        except Exception as e:
            logger.error("Failed to send report via email: %s", e)
            return {"status": "ERROR", "message": str(e)}

    def _resolve_email(self, target: str) -> Optional[str]:
        """Resolve a target name to an email address.

        Args:
            target: Target identifier.

        Returns:
            Email address or None.
        """
        targets = {
            "ic3": self.config.ic3_email,
            "apwg": self.config.apwg_email,
        }
        targets.update(self.config.registrar_abuse_emails)
        return targets.get(target)

    def _save_report_txt(self, report: Dict) -> Optional[str]:
        """Save report as a text file.

        Args:
            report: Report dict.

        Returns:
            Path to saved file or None.
        """
        try:
            path = self.report_dir / f"report_{report['report_id']}.txt"
            with open(path, "w") as f:
                f.write(f"OMNITRADER SENTINEL REPORT\n")
                f.write(f"Report ID: {report['report_id']}\n")
                f.write(f"Type: {report['type']}\n")
                f.write(f"Generated: {report['generated_at']}\n")
                f.write(f"Severity: {report['severity']}\n\n")
                f.write(report["description"])
                f.write(f"\n\nEvidence:\n")
                f.write(json.dumps(report["evidence"], indent=2))
            return str(path)
        except Exception as e:
            logger.error("Failed to save report: %s", e)
            return None

    def _log_submission(self, report: Dict, target_email: str) -> None:
        """Log the submission to the database.

        Args:
            report: Report dict.
            target_email: Email address that was sent to.
        """
        session = get_session()
        try:
            submission = BountySubmission(
                event_type="sentinel_report",
                target_address=report.get("evidence", {}).get("target_address", ""),
                evidence_summary=report.get("summary", ""),
                submitted_to=target_email,
                status="SUBMITTED",
                submission_date=datetime.now(timezone.utc),
                notes=json.dumps({"report_id": report["report_id"]}),
            )
            session.add(submission)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to log submission: %s", e)
        finally:
            session.close()

    # ── Auto-Reporting ────────────────────────────────────────────────

    def _severity_level(self, severity: str) -> int:
        """Return numeric severity level for comparison."""
        levels = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        return levels.get(severity.upper(), -1)

    def _meets_threshold(self, severity: str) -> bool:
        """Check if severity meets the auto-reporting threshold."""
        return self._severity_level(severity) >= self._severity_level(
            AUTO_REPORT_MIN_SEVERITY
        )

    def _check_and_submit_unsubmitted(self) -> Dict:
        """Check for unsubmitted HoneypotEvents and OnChainAlerts,
        and auto-submit those meeting the severity threshold.

        Returns:
            Summary dict with counts of processed alerts.
        """
        summary = {
            "honeypot_checked": 0,
            "honeypot_submitted": 0,
            "onchain_checked": 0,
            "onchain_submitted": 0,
            "errors": 0,
        }

        session = get_session()
        try:
            # 1. Check HoneypotEvents — treat all as phishing-like activity
            honeypots = session.query(HoneypotEvent).order_by(
                HoneypotEvent.timestamp.asc()
            ).all()

            for hp in honeypots:
                summary["honeypot_checked"] += 1
                if hp.fake_key_used or hp.method in ("GET", "POST"):
                    alert = OnChainAlert(
                        alert_type="honeypot_detected",
                        network="local",
                        severity="HIGH",
                        target_address=hp.ip_address,
                        wallet_addresses=json.dumps([hp.ip_address]),
                        tx_hashes=None,
                        evidence=json.dumps({
                            "method": hp.method,
                            "path": hp.path,
                            "route": hp.route,
                            "fake_key": hp.fake_key_used,
                            "user_agent": hp.user_agent,
                        }),
                        created_at=datetime.utcnow(),
                    )
                    report = self.format_report(alert, report_type="phishing")
                    result = self.send_report(report, target="ic3")
                    if result.get("status") in ("SENT", "DRY_RUN"):
                        summary["honeypot_submitted"] += 1
                        logger.info(
                            "Auto-submitted honeypot report: %s -> %s",
                            hp.id, result.get("report_id", "N/A"),
                        )
                    else:
                        summary["errors"] += 1
                        logger.error(
                            "Auto-submit honeypot failed: %s -> %s",
                            hp.id, result.get("message"),
                        )

            # 2. Check OnChainAlerts with severity >= threshold
            alerts = session.query(OnChainAlert).filter(
                OnChainAlert.severity.in_(["HIGH", "CRITICAL"])
            ).filter(
                OnChainAlert.submitted_as_bounty == False
            ).order_by(OnChainAlert.created_at.asc()).all()

            for alert in alerts:
                summary["onchain_checked"] += 1
                if self._meets_threshold(alert.severity):
                    report = self.format_report(alert, report_type="general")
                    result = self.send_report(report, target="ic3")
                    if result.get("status") in ("SENT", "DRY_RUN"):
                        summary["onchain_submitted"] += 1
                        alert.submitted_as_bounty = True
                        logger.info(
                            "Auto-submitted alert: %s (%s) -> %s",
                            alert.id, alert.alert_type, result.get("report_id", "N/A"),
                        )
                    else:
                        summary["errors"] += 1
                        logger.error(
                            "Auto-submit alert failed: %s -> %s",
                            alert.id, result.get("message"),
                        )

        except Exception as e:
            logger.error("Auto-report check failed: %s", e)
            summary["errors"] += 1
        finally:
            try:
                session.commit()
            except Exception:
                session.rollback()
            session.close()

        return summary

    def _post_telegram_summary(self, summary: Dict) -> None:
        """Post a summary of the auto-report cycle to Telegram.

        Args:
            summary: Summary dict from _check_and_submit_unsubmitted.
        """
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.debug("Telegram not configured — skipping summary post.")
            return

        total_submitted = (
            summary.get("honeypot_submitted", 0)
            + summary.get("onchain_submitted", 0)
        )
        total_checked = (
            summary.get("honeypot_checked", 0)
            + summary.get("onchain_checked", 0)
        )

        text = (
            f"*Sentinel Auto-Report Cycle*\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"---\n"
            f"Honeypots checked: {summary.get('honeypot_checked', 0)}\n"
            f"Honeypots submitted: {summary.get('honeypot_submitted', 0)}\n"
            f"OnChain alerts checked: {summary.get('onchain_checked', 0)}\n"
            f"OnChain alerts submitted: {summary.get('onchain_submitted', 0)}\n"
            f"Errors: {summary.get('errors', 0)}\n"
            f"---\n"
            f"Total submitted this cycle: {total_submitted}"
        )

        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                logger.info("Telegram summary posted successfully.")
            else:
                logger.warning(
                    "Telegram post failed (HTTP %s): %s",
                    resp.status_code, resp.text[:200],
                )
        except Exception as e:
            logger.error("Telegram summary post failed: %s", e)

    def run_auto_report_cycle(self) -> Dict:
        """Run a full auto-report cycle: check DB, submit, post to Telegram.

        Returns:
            Summary dict.
        """
        logger.info("Starting auto-report cycle...")
        summary = self._check_and_submit_unsubmitted()
        self._post_telegram_summary(summary)
        logger.info(
            "Auto-report cycle complete: %s",
            json.dumps(summary),
        )
        return summary

    # ── Background Scheduler ──────────────────────────────────────────

    _scheduler_thread: Optional[threading.Thread] = None
    _scheduler_running: bool = False

    def start_auto_report_scheduler(self) -> None:
        """Start the background auto-report scheduler in a daemon thread."""
        if self._scheduler_running:
            logger.warning("Auto-report scheduler already running.")
            return

        self._scheduler_running = True

        def _scheduler_loop():
            logger.info(
                "Auto-report scheduler started (interval=%ds)",
                AUTO_REPORT_INTERVAL_SEC,
            )
            while self._scheduler_running:
                try:
                    self.run_auto_report_cycle()
                except Exception as e:
                    logger.error("Auto-report cycle error: %s", e)
                # Sleep in small increments so we can stop gracefully
                for _ in range(AUTO_REPORT_INTERVAL_SEC):
                    if not self._scheduler_running:
                        break
                    time.sleep(1)
            logger.info("Auto-report scheduler stopped.")

        self._scheduler_thread = threading.Thread(
            target=_scheduler_loop, daemon=True
        )
        self._scheduler_thread.start()
        logger.info("Auto-report scheduler thread started.")

    def stop_auto_report_scheduler(self) -> None:
        """Stop the background auto-report scheduler."""
        self._scheduler_running = False
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=60)
            self._scheduler_thread = None
        logger.info("Auto-report scheduler stopped.")


# ── Module-level singleton loader ─────────────────────────────────────

def get_reporter(config: SanctionReporterConfig = None) -> SanctionReporter:
    """Get or create the SanctionReporter singleton.

    Args:
        config: Optional configuration override.

    Returns:
        SanctionReporter instance.
    """
    return SanctionReporter.load()