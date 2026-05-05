"""Bounty Reporter for Sleuth Module.

Formats evidence into reports and submits to bounty programs,
government whistleblower offices, and class-action lead services.
"""

import os
import json
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, List, Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, ListFlowable, ListItem
)
from reportlab.lib import colors
from reportlab.lib.units import inch

from ..utils.db import BountySubmission, get_session
from ..utils.logging_config import get_logger

logger = get_logger("sleuth.bounty")


class WhistleblowerTarget:
    """Represents a government or bounty program target for submissions."""

    def __init__(
        self,
        name: str,
        email: str,
        description: str,
        submission_format: str = "pdf",
        requires_pgp: bool = False,
        max_attachment_size_mb: int = 10,
    ):
        """Initialize a whistleblower target.

        Args:
            name: Target organization name.
            email: Submission email address.
            description: What types of violations this target accepts.
            submission_format: Preferred format (pdf, json, mixed).
            requires_pgp: Whether PGP encryption is required.
            max_attachment_size_mb: Maximum attachment size in MB.
        """
        self.name = name
        self.email = email
        self.description = description
        self.submission_format = submission_format
        self.requires_pgp = requires_pgp
        self.max_attachment_size_mb = max_attachment_size_mb

    def format_report(
        self,
        evidence: Dict,
        report_type: str = "violation",
    ) -> Dict:
        """Format evidence into a report suitable for this target.

        Args:
            evidence: Evidence dict with violations, timelines, etc.
            report_type: Type of report (violation, fraud, compliance).

        Returns:
            Dict with formatted report data.
        """
        report = {
            "target": self.name,
            "report_type": report_type,
            "submitted_at": datetime.utcnow().isoformat(),
            "evidence_summary": evidence.get("summary", ""),
            "violations": evidence.get("violations", []),
            "timeline": evidence.get("timeline", []),
            "affected_parties": evidence.get("affected_parties", []),
            "evidence_links": evidence.get("links", []),
        }
        return report


class BountyReporter:
    """Formats and submits bounty reports to various programs."""

    # Known bounty program targets
    BOUNTY_PROGRAMS = {
        "ftc": WhistleblowerTarget(
            name="FTC (Federal Trade Commission)",
            email="reportfraud@ftc.gov",
            description="Consumer protection violations, data broker misconduct, "
                        "privacy violations, deceptive trade practices",
            submission_format="pdf",
        ),
        "sec": WhistleblowerTarget(
            name="SEC (Securities and Exchange Commission)",
            email="tipcmt@sec.gov",
            description="Securities fraud, insider trading, market manipulation, "
                        "unregistered securities offerings",
            submission_format="pdf",
        ),
        "cftc": WhistleblowerTarget(
            name="CFTC (Commodity Futures Trading Commission)",
            email="whistleblower@cftc.gov",
            description="Crypto commodity fraud, market manipulation, "
                        "unregistered futures/derivatives trading",
            submission_format="pdf",
        ),
        "doj": WhistleblowerTarget(
            name="DOJ (Department of Justice)",
            email="main@usdoj.gov",
            description="False Claims Act, corporate fraud, cybercrime, "
                        "money laundering, sanctions violations",
            submission_format="pdf",
            requires_pgp=True,
        ),
        "arbitrum_dao": WhistleblowerTarget(
            name="Arbitrum DAO Bounty",
            email="bounties@arbitrum.io",
            description="Smart contract exploits, DeFi protocol vulnerabilities, "
                        "bridge exploits, governance attacks",
            submission_format="mixed",
        ),
        "binance_security": WhistleblowerTarget(
            name="Binance Bug Bounty",
            email="security@binance.com",
            description="Exchange vulnerabilities, smart contract bugs, "
                        "API security issues",
            submission_format="pdf",
        ),
    }

    _instance = None

    def __init__(self, config: Dict = None):
        """Initialize the bounty reporter.

        Args:
            config: Configuration with email settings and submission options.
        """
        self.config = config or {}
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")
        self.sender_email = os.environ.get("SENDER_EMAIL", "")
        self.submissions_enabled = self.config.get("bounty_submissions", {}).get(
            "enabled", True
        )
        self.pgp_private_key = os.environ.get("PGP_PRIVATE_KEY", "")

        # Track submissions to avoid duplicates
        self.submission_ids: set = set()

    @classmethod
    def load(cls, config: Dict = None) -> "BountyReporter":
        """Singleton loader for BountyReporter.

        Args:
            config: Optional configuration.

        Returns:
            BountyReporter instance.
        """
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    def generate_pdf_report(
        self,
        evidence: Dict,
        target: WhistleblowerTarget,
        report_id: str = None,
    ) -> bytes:
        """Generate a PDF report from evidence data.

        Args:
            evidence: Evidence dict.
            target: Target organization.
            report_id: Optional report identifier.

        Returns:
            PDF file as bytes.
        """
        report_id = report_id or f"bounty_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        filename = f"{report_id}.pdf"

        doc = SimpleDocTemplate(
            filename,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72,
        )

        styles = getSampleStyleSheet()
        story = []

        # Title
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=18,
            spaceAfter=30,
            textColor=colors.HexColor("#1a1a2e"),
        )
        story.append(Paragraph("WHISTLEBLOWER BOUNTY REPORT", title_style))
        story.append(Spacer(1, 12))

        # Metadata table
        meta_data = [
            ["Report ID:", report_id],
            ["Target:", target.name],
            ["Date:", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")],
            ["Classification:", "CONFIDENTIAL"],
            ["Report Type:", evidence.get("report_type", "violation")],
        ]
        meta_table = Table(meta_data, colWidths=[1.5 * inch, 4.5 * inch])
        meta_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 20))

        # Executive Summary
        story.append(Paragraph("EXECUTIVE SUMMARY", styles["Heading2"]))
        summary = evidence.get("summary", "No summary provided.")
        story.append(Paragraph(summary, styles["Normal"]))
        story.append(Spacer(1, 15))

        # Violations section
        violations = evidence.get("violations", [])
        if violations:
            story.append(Paragraph("VIOLATIONS IDENTIFIED", styles["Heading2"]))
            for i, violation in enumerate(violations, 1):
                v_text = f"<b>{violation.get('type', 'Unknown')}</b>: {violation.get('description', '')}"
                if violation.get("severity"):
                    v_text += f" <i>[Severity: {violation['severity']}]</i>"
                story.append(Paragraph(f"{i}. {v_text}", styles["Normal"]))
                if violation.get("statute"):
                    story.append(Paragraph(
                        f"   Relevant statute: {violation['statute']}",
                        styles["Normal"],
                    ))
                story.append(Spacer(1, 5))
            story.append(Spacer(1, 10))

        # Timeline
        timeline = evidence.get("timeline", [])
        if timeline:
            story.append(Paragraph("TIMELINE OF EVENTS", styles["Heading2"]))
            timeline_data = [["Date", "Event", "Evidence"]]
            for entry in timeline:
                timeline_data.append([
                    entry.get("date", ""),
                    entry.get("event", ""),
                    entry.get("evidence_reference", ""),
                ])
            timeline_table = Table(timeline_data, colWidths=[1.5 * inch, 3 * inch, 2.5 * inch])
            timeline_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(timeline_table)
            story.append(Spacer(1, 15))

       # On-chain evidence links
        links = evidence.get("links", [])
        if links:
            story.append(Paragraph("ON-CHAIN EVIDENCE", styles["Heading2"]))
            for link in links:
                url = link.get("url", "")
                label = link.get("label", url)
                story.append(Paragraph(
                    f'<a href="{url}">{label}</a>',
                    styles["Normal"],
                ))
            story.append(Spacer(1, 15))

        # Addresses of interest
        addresses = evidence.get("addresses", [])
        if addresses:
            story.append(Paragraph("ADDRESSES OF INTEREST", styles["Heading2"]))
            addr_data = [["Address", "Role", "Chain"]]
            for addr in addresses:
                addr_data.append([
                    addr.get("address", "")[:20] + "...",
                    addr.get("role", ""),
                    addr.get("chain", ""),
                ])
            addr_table = Table(addr_data, colWidths=[2.5 * inch, 1.5 * inch, 2 * inch])
            addr_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(addr_table)

        # Disclaimer
        story.append(Spacer(1, 30))
        disclaimer_style = ParagraphStyle(
            "Disclaimer",
            parent=styles["Normal"],
            fontSize=8,
            textColor=colors.grey,
            leading=10,
        )
        story.append(Paragraph(
            "DISCLAIMER: This report was generated by an automated system "
            "for the purpose of collecting evidence for bounty programs and "
            "regulatory submissions. It does not constitute legal advice. "
            "All findings should be independently verified before action.",
            disclaimer_style,
        ))

        doc.build(story)
        logger.info("PDF report generated: %s", filename)
        return open(filename, "rb").read()

    def generate_json_report(
        self,
        evidence: Dict,
        target: WhistleblowerTarget,
    ) -> bytes:
        """Generate a structured JSON report.

        Args:
            evidence: Evidence dict.
            target: Target organization.

        Returns:
            JSON bytes.
        """
        report = {
            "meta": {
                "report_id": f"bounty_json_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                "target": target.name,
                "format_version": "1.0",
                "generated_at": datetime.utcnow().isoformat(),
            },
            "evidence": {
                "summary": evidence.get("summary", ""),
                "violations": evidence.get("violations", []),
                "timeline": evidence.get("timeline", []),
                "addresses": evidence.get("addresses", []),
                "links": evidence.get("links", []),
            },
            "analysis": {
                "risk_level": evidence.get("severity", "medium"),
                "confidence": evidence.get("confidence", 0.7),
                "recommended_action": evidence.get("recommended_action", "review"),
            },
        }

        json_bytes = json.dumps(report, indent=2).encode("utf-8")
        logger.info("JSON report generated")
        return json_bytes

    def submit_report(
        self,
        evidence: Dict,
        target_name: str = "ftc",
        report_type: str = "violation",
        dry_run: bool = True,
    ) -> Optional[Dict]:
        """Submit a bounty report to the specified target.

        Args:
            evidence: Evidence dict with all findings.
            target_name: Target identifier (ftc, sec, cftc, etc.).
            report_type: Type of report.
            dry_run: If True, only log without sending.

        Returns:
            Submission record dict, or None on failure.
        """
        if not self.submissions_enabled:
            logger.warning("Submissions disabled. Skipping.")
            return None

        target = self.BOUNTY_PROGRAMS.get(target_name)
        if target is None:
            logger.error("Unknown target: %s", target_name)
            return None

        report_id = f"{target_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        # Generate reports
        if target.submission_format in ("pdf", "mixed"):
            pdf_data = self.generate_pdf_report(evidence, target, report_id)

        if target.submission_format in ("json", "mixed"):
            json_data = self.generate_json_report(evidence, target)

        if dry_run:
            logger.info(
                "DRY RUN: Would submit %s report to %s (%s)",
                target_name, target.email, target.name,
            )
            submission = {
                "id": report_id,
                "target": target_name,
                "target_email": target.email,
                "target_name": target.name,
                "report_type": report_type,
                "dry_run": True,
                "status": "DRY_RUN",
                "submitted_at": datetime.utcnow().isoformat(),
            }
            self._log_submission(submission)
            return submission

        # Actual submission via email
        try:
            success = self._send_email_submission(
                target=target,
                report_id=report_id,
                pdf_data=pdf_data if target.submission_format in ("pdf", "mixed") else None,
                json_data=json_data if target.submission_format in ("json", "mixed") else None,
                evidence=evidence,
            )

            status = "SENT" if success else "FAILED"
            submission = {
                "id": report_id,
                "target": target_name,
                "target_email": target.email,
                "target_name": target.name,
                "report_type": report_type,
                "dry_run": False,
                "status": status,
                "submitted_at": datetime.utcnow().isoformat(),
            }
            self._log_submission(submission)
            return submission

        except Exception as e:
            logger.error("Failed to submit report: %s", e)
            return None

    def _send_email_submission(
        self,
        target: WhistleblowerTarget,
        report_id: str,
        pdf_data: bytes = None,
        json_data: bytes = None,
        evidence: Dict = None,
    ) -> bool:
        """Send a report via email.

        Args:
            target: Target organization.
            report_id: Report identifier.
            pdf_data: PDF report bytes.
            json_data: JSON report bytes.
            evidence: Evidence dict for the email body.

        Returns:
            True if sent successfully.
        """
        if not self.smtp_user or not self.smtp_password:
            logger.error("SMTP credentials not configured.")
            return False

        msg = MIMEMultipart()
        msg["From"] = self.sender_email
        msg["To"] = target.email
        msg["Subject"] = f"[Bounty Report] {report_id} - {target.name}"

        body = f"""
        Whistleblower Bounty Report
        ============================

        Report ID: {report_id}
        Target: {target.name}
        Type: {evidence.get('report_type', 'violation') if evidence else 'violation'}

        Summary:
        {evidence.get('summary', 'No summary provided.') if evidence else 'No summary provided.'}

        This report contains on-chain evidence and analysis
        related to potential violations. Detailed findings
        are attached.

        DISCLAIMER: This is an automated submission. All findings
        should be independently verified.
        """

        msg.attach(MIMEText(body, "plain"))

        # Attach PDF if available
        if pdf_data:
            pdf_attachment = MIMEBase("application", "pdf")
            pdf_attachment.set_payload(pdf_data)
            encoders.encode_base64(pdf_attachment)
            pdf_attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=f"{report_id}.pdf",
            )
            msg.attach(pdf_attachment)

        # Attach JSON if available
        if json_data:
            json_attachment = MIMEBase("application", "json")
            json_attachment.set_payload(json_data)
            encoders.encode_base64(json_attachment)
            json_attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=f"{report_id}.json",
            )
            msg.attach(json_attachment)

        try:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.sender_email, [target.email], msg.as_string())
            server.quit()
            logger.info("Report sent to %s", target.email)
            return True
        except Exception as e:
            logger.error("Failed to send email to %s: %s", target.email, e)
            return False

    def _log_submission(self, submission: Dict) -> None:
        """Log submission to the database.

        Args:
            submission: Submission record dict.
        """
        session = get_session()
        try:
            record = BountySubmission(
                report_id=submission.get("id", ""),
                target=submission.get("target", ""),
                target_email=submission.get("target_email", ""),
                target_name=submission.get("target_name", ""),
                report_type=submission.get("report_type", ""),
                status=submission.get("status", "UNKNOWN"),
                dry_run=submission.get("dry_run", True),
                submission_date=datetime.utcnow(),
            )
            session.add(record)
            session.commit()
            self.submission_ids.add(submission.get("id", ""))
        except Exception as e:
            session.rollback()
            logger.error("Failed to log submission: %s", e)
        finally:
            session.close()

    def get_submission_history(self) -> List[Dict]:
        """Get submission history from the database.

        Returns:
            List of submission records.
        """
        from ..utils.db import BountySubmission
        session = get_session()
        try:
            records = session.query(BountySubmission).order_by(
                BountySubmission.submission_date.desc()
            ).limit(50).all()

            return [
                {
                    "id": r.id,
                    "target": r.submitted_to,
                    "target_name": r.submitted_to,
                    "report_type": r.event_type,
                    "status": r.status,
                    "dry_run": False,
                    "submitted_at": r.submission_date.isoformat() if r.submission_date else None,
                    "network": r.network,
                    "target_address": r.target_address,
                    "evidence_summary": r.evidence_summary,
                    "report_path": r.report_path,
                    "value_at_risk": r.value_at_risk,
                }
                for r in records
            ]
        finally:
            session.close()

    def format_evidence_for_target(
        self,
        alert: Dict,
        target_name: str = "ftc",
    ) -> Dict:
        """Format a scanner alert into evidence suitable for a specific target.

        Handles both raw alert dicts and OnChainAlert model output (to_dict()).

        Args:
            alert: Alert dict from the on-chain scanner.
            target_name: Target identifier.

        Returns:
            Formatted evidence dict.
        """
        target = self.BOUNTY_PROGRAMS.get(target_name)

        # Normalize field names (OnChainAlert uses different names)
        chain = alert.get("chain") or alert.get("network", "ethereum")
        tx_hashes_raw = alert.get("tx_hashes") or []
        if isinstance(tx_hashes_raw, str):
            try:
                import json
                tx_hashes_raw = json.loads(tx_hashes_raw)
            except (json.JSONDecodeError, TypeError):
                tx_hashes_raw = [tx_hashes_raw] if tx_hashes_raw else []

        addresses = alert.get("addresses", [])
        if not addresses:
            wallet_addrs = alert.get("wallet_addresses", [])
            if isinstance(wallet_addrs, str):
                try:
                    import json
                    wallet_addrs = json.loads(wallet_addrs)
                except (json.JSONDecodeError, TypeError):
                    wallet_addrs = []
            addresses = [{"address": a, "role": "involved", "chain": chain} for a in wallet_addrs]

        evidence_text = alert.get("evidence", {})
        if isinstance(evidence_text, str):
            try:
                import json
                evidence_text = json.loads(evidence_text)
            except (json.JSONDecodeError, TypeError):
                evidence_text = {}

        summary = alert.get("summary") or evidence_text.get("summary", "No summary.")

        evidence = {
            "report_type": self._get_report_type_for_target(target_name),
            "summary": summary,
            "violations": self._classify_violations(alert, target_name),
            "timeline": self._build_timeline(alert),
            "addresses": addresses,
            "links": self._build_evidence_links({
                "chain": chain,
                "tx_hashes": tx_hashes_raw,
                "addresses": [a.get("address", a) if isinstance(a, dict) else a for a in addresses],
            }),
            "severity": alert.get("severity", "medium"),
            "confidence": alert.get("confidence", 0.7),
            "recommended_action": self._get_recommended_action(target_name, alert),
        }
        return evidence

    def _get_report_type_for_target(self, target_name: str) -> str:
        """Get the appropriate report type for a target."""
        types = {
            "ftc": "consumer_protection_violation",
            "sec": "securities_violation",
            "cftc": "commodity_fraud",
            "doj": "fraud_and_fraudulent_activity",
            "arbitrum_dao": "smart_contract_vulnerability",
            "binance_security": "security_vulnerability",
        }
        return types.get(target_name, "general_violation")

    def _classify_violations(self, alert: Dict, target_name: str) -> List[Dict]:
        """Classify alert findings into specific violations."""
        violations = []
        alert_type = alert.get("alert_type", "")

        if target_name == "ftc":
            if "data_broker" in alert_type.lower():
                violations.append({
                    "type": "CCPA/GDPR Data Broker Violation",
                    "description": alert.get("summary", ""),
                    "severity": alert.get("severity", "high"),
                    "statute": "Cal. Civ. Code § 1798.100 et seq. / GDPR Art. 6",
                })
            else:
                violations.append({
                    "type": "Deceptive Trade Practice",
                    "description": alert.get("summary", ""),
                    "severity": alert.get("severity", "medium"),
                    "statute": "FTC Act § 5, 15 U.S.C. § 45",
                })
        elif target_name in ("sec", "cftc"):
            violations.append({
                "type": "Market Manipulation / Fraud",
                "description": alert.get("summary", ""),
                "severity": alert.get("severity", "high"),
                "statute": "Securities Exchange Act § 10(b) / CEA § 6(c)",
            })
        else:
            violations.append({
                "type": "On-Chain Violation",
                "description": alert.get("summary", ""),
                "severity": alert.get("severity", "medium"),
            })

        return violations

    def _build_timeline(self, alert: Dict) -> List[Dict]:
        """Build a timeline from alert evidence."""
        timeline = []

        evidence = alert.get("evidence", {})
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except json.JSONDecodeError:
                evidence = {}

        # Extract timeline events from evidence
        if isinstance(evidence, dict):
            for key, value in evidence.items():
                if isinstance(value, dict):
                    date = value.get("timestamp", value.get("date", ""))
                    event = value.get("description", value.get("event", key))
                    timeline.append({
                        "date": date,
                        "event": event,
                        "evidence_reference": value.get("tx_hash", value.get("link", "")),
                    })

        # Add the alert discovery time if no timeline events exist
        if not timeline:
            timeline.append({
                "date": datetime.utcnow().isoformat(),
                "event": f"Alert detected: {alert.get('alert_type', 'Unknown')}",
                "evidence_reference": alert.get("tx_hashes", ["N/A"])[0] if alert.get("tx_hashes") else "N/A",
            })

        return timeline

    def _build_evidence_links(self, alert: Dict) -> List[Dict]:
        """Build evidence links from alert data."""
        links = []

        chain = alert.get("chain", "ethereum")
        explorer_base = {
            "ethereum": "https://etherscan.io",
            "arbitrum": "https://arbiscan.io",
            "bsc": "https://bscscan.com",
        }

        base = explorer_base.get(chain, "https://etherscan.io")

        for tx_hash in alert.get("tx_hashes", []):
            links.append({
                "label": f"TX: {tx_hash[:20]}...",
                "url": f"{base}/tx/{tx_hash}",
            })

        for addr in alert.get("addresses", []):
            links.append({
                "label": f"Address: {addr[:20]}...",
                "url": f"{base}/address/{addr}",
            })

        return links

    def _get_recommended_action(
        self, target_name: str, alert: Dict
    ) -> str:
        """Get recommended action for a given target and alert."""
        severity = alert.get("severity", "medium")

        actions = {
            "ftc": "Submit to FTC with all supporting evidence. Consider joining class-action if multiple victims identified.",
            "sec": "File SEC whistleblower tip with detailed on-chain analysis. Qualify for 10-30% bounty if enforcement action results.",
            "cftc": "Submit to CFTC Office of Enforcement. CFTC offers up to 30% of recovered funds for whistleblower tips.",
            "doj": "Submit via FBI tip line for criminal matters. Consider False Claims Act if government funds are involved.",
            "arbitrum_dao": "Submit to Arbitrum DAO bounty program with full technical report and reproduction steps.",
            "binance_security": "Submit through Binance bug bounty portal with proof-of-concept and remediation suggestions.",
        }
        return actions.get(target_name, "Review and submit with supporting evidence.")

    def generate_draft_report(self, alert: Dict, target_name: str = "cftc") -> Optional[Dict]:
        """Generate a draft bounty report from an OnChainAlert.

        Creates a PDF report in reports/, records a draft BountySubmission,
        and links it to the alert.

        Args:
            alert: OnChainAlert dict (or model instance with to_dict()).
            target_name: Target program key (cftc, sec, ftc, etc.).

        Returns:
            Draft submission record dict, or None on failure.
        """
        import io
        from ..utils.db import OnChainAlert as AlertModel

        # Convert model instance to dict if needed
        if hasattr(alert, "to_dict"):
            alert_data = alert.to_dict()
        else:
            alert_data = dict(alert)

        # Get the bounty reporter singleton
        reporter = self.load()

        # Get target
        target = reporter.BOUNTY_PROGRAMS.get(target_name)
        if target is None:
            logger.error("Unknown bounty target: %s", target_name)
            return None

        # Format evidence from alert
        evidence = reporter.format_evidence_for_target(alert_data, target_name)

        # Generate PDF to memory
        report_id = f"bounty_draft_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{alert_data.get('id', 'unknown')}"

        # Create reports directory if needed
        reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "reports")
        os.makedirs(reports_dir, exist_ok=True)
        pdf_path = os.path.join(reports_dir, f"{report_id}.pdf")

        # Build PDF in memory then save to disk
        doc = SimpleDocTemplate(
            io.BytesIO(),
            pagesize=letter,
            rightMargin=72, leftMargin=72,
            topMargin=72, bottomMargin=72,
        )
        styles = getSampleStyleSheet()
        story = []

        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=18, spaceAfter=30,
            textColor=colors.HexColor("#1a1a2e"),
        )
        story.append(Paragraph("WHISTLEBLOWER BOUNTY REPORT — DRAFT", title_style))
        story.append(Spacer(1, 12))

        meta_data = [
            ["Report ID:", report_id],
            ["Target:", target.name],
            ["Date:", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")],
            ["Classification:", "DRAFT — Not Submitted"],
            ["Alert Type:", alert_data.get("alert_type", "unknown")],
        ]
        meta_table = Table(meta_data, colWidths=[1.5 * inch, 4.5 * inch])
        meta_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(meta_table)
        story.append(Spacer(1, 20))

        story.append(Paragraph("EXECUTIVE SUMMARY", styles["Heading2"]))
        story.append(Paragraph(evidence.get("summary", "No summary."), styles["Normal"]))
        story.append(Spacer(1, 15))

        violations = evidence.get("violations", [])
        if violations:
            story.append(Paragraph("VIOLATIONS IDENTIFIED", styles["Heading2"]))
            for i, v in enumerate(violations, 1):
                v_text = f"<b>{v.get('type', 'Unknown')}</b>: {v.get('description', '')}"
                if v.get("severity"):
                    v_text += f" <i>[Severity: {v['severity']}]</i>"
                story.append(Paragraph(f"{i}. {v_text}", styles["Normal"]))
                if v.get("statute"):
                    story.append(Paragraph(f"   Statute: {v['statute']}", styles["Normal"]))
                story.append(Spacer(1, 5))
            story.append(Spacer(1, 10))

        timeline = evidence.get("timeline", [])
        if timeline:
            story.append(Paragraph("TIMELINE OF EVENTS", styles["Heading2"]))
            td = [["Date", "Event", "Evidence"]]
            for e in timeline:
                td.append([e.get("date", ""), e.get("event", ""), e.get("evidence_reference", "")])
            tt = Table(td, colWidths=[1.5 * inch, 3 * inch, 2.5 * inch])
            tt.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ]))
            story.append(tt)
            story.append(Spacer(1, 15))

        links = evidence.get("links", [])
        if links:
            story.append(Paragraph("ON-CHAIN EVIDENCE", styles["Heading2"]))
            for link in links:
                url = link.get("url", "")
                label = link.get("label", url)
                story.append(Paragraph(
                    f'<a href="{url}">{label}</a>',
                    styles["Normal"],
                ))
            story.append(Spacer(1, 15))

        story.append(Spacer(1, 30))
        dis_style = ParagraphStyle(
            "Disclaimer", parent=styles["Normal"],
            fontSize=8, textColor=colors.grey, leading=10,
        )
        story.append(Paragraph(
            "DISCLAIMER: DRAFT report generated by Sleuth module. "
            "Not yet reviewed or submitted. For internal use only.",
            dis_style,
        ))

        pdf_buf = io.BytesIO()
        doc = SimpleDocTemplate(pdf_buf, pagesize=letter,
                                rightMargin=72, leftMargin=72,
                                topMargin=72, bottomMargin=72)
        doc.build(story)
        pdf_bytes = pdf_buf.getvalue()

        # Save to reports/
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        logger.info("Draft PDF report generated: %s", pdf_path)

        # Record draft submission in DB
        session = get_session()
        try:
            submission = BountySubmission(
                event_type=alert_data.get("alert_type", "unknown"),
                network=alert_data.get("network", ""),
                target_address=alert_data.get("target_address"),
                evidence_summary=evidence.get("summary", ""),
                report_path=pdf_path,
                submitted_to=target_name,
                status="DRAFT",
                value_at_risk=alert_data.get("value_usd"),
            )
            session.add(submission)
            session.commit()

            # Link to alert if alert is a model instance
            if hasattr(alert, "id"):
                alert_obj = session.query(AlertModel).filter_by(id=alert.id).first()
                if alert_obj:
                    alert_obj.submitted_as_bounty = True
                    alert_obj.bounty_submission_id = submission.id

            session.commit()
            logger.info("Draft submission recorded: id=%s report_id=%s", submission.id, report_id)

            return {
                "id": submission.id,
                "report_id": report_id,
                "target": target_name,
                "target_name": target.name,
                "status": "DRAFT",
                "pdf_path": pdf_path,
                "alert_id": alert_data.get("id"),
                "event_type": evidence.get("report_type", "violation"),
            }
        except Exception as e:
            session.rollback()
            logger.error("Failed to record draft submission: %s", e)
            return None
        finally:
            session.close()

    def get_draft_reports(self) -> List[Dict]:
        """Get all draft bounty reports.

        Returns:
            List of draft submission records.
        """
        from ..utils.db import BountySubmission
        session = get_session()
        try:
            records = session.query(BountySubmission).filter_by(status="DRAFT").order_by(
                BountySubmission.created_at.desc()
            ).all()
            return [r.to_dict() for r in records]
        finally:
            session.close()

    def approve_report(self, report_id: int) -> Optional[Dict]:
        """Approve a draft bounty report (set status to APPROVED).

        Args:
            report_id: BountySubmission.id

        Returns:
            Updated submission record dict, or None on failure.
        """
        from ..utils.db import BountySubmission
        session = get_session()
        try:
            submission = session.query(BountySubmission).filter_by(id=report_id).first()
            if not submission:
                logger.warning("BountySubmission id=%s not found", report_id)
                return None
            submission.status = "APPROVED"
            submission.updated_at = datetime.utcnow()
            session.commit()
            logger.info("Bounty report approved: id=%s", report_id)
            return submission.to_dict()
        except Exception as e:
            session.rollback()
            logger.error("Failed to approve report id=%s: %s", report_id, e)
            return None
        finally:
            session.close()
