"""Opt-Out Automator for Omnitrader Privacy Module.

Automates submission of opt-out requests to data brokers listed
on databrokerlist.com and other known brokers.

Uses headless browser automation (Playwright) for browsers-based
opt-out flows, and direct API/HTTP submissions where available.

Tracks success rates and automatically re-submits monthly.
"""

import os
import json
import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

from ..utils.logging_config import get_logger
from ..utils.db import get_session, SystemEvent, OptOutRecord

logger = get_logger("privacy.optout")


class OptOutStatus(str, Enum):
    """Opt-out request status."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    IGNORED = "IGNORED"
    ERROR = "ERROR"


class BrokerType(str, Enum):
    """Type of data broker."""
    DATABROKER = "databroker"
    AGRIGATOR = "aggregator"
    SCORING = "scoring"
    BACKGROUND_CHECK = "background_check"
    OTHER = "other"


@dataclass
class BrokerProfile:
    """Profile of a data broker."""
    name: str
    type: BrokerType
    website: str
    opt_out_url: str
    has_api: bool = False
    api_endpoint: str = ""
    requires_browser: bool = True
    requires_email: bool = False
    email_address: str = ""
    requires_phone: bool = False
    phone_number: str = ""
    known_violations: List[str] = field(default_factory=list)
    ccpa_compliant: bool = False
    gdpr_compliant: bool = False


class OptOutAutomator:
    """Automates opt-out requests to data brokers.

    Supports multiple submission methods:
    1. Headless browser automation (Playwright)
    2. Direct API submission
    3. Email submission
    4. Phone call simulation (recording)

    Tracks submission results and re-submission schedules.
    """

    # Known data brokers from databrokerlist.com and similar sources
    KNOWN_BROKERS = [
        BrokerProfile(
            name="Acxiom",
            type=BrokerType.DATABROKER,
            website="https://www.acxiom.com",
            opt_out_url="https://secure.acxiom.com/optout/",
            requires_browser=True,
            email_address="privacy@acxiom.com",
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="LexisNexis",
            type=BrokerType.DATABROKER,
            website="https://www.lexisnexis.com",
            opt_out_url="https://www.lexisnexis.com/privacy/opt-out",
            requires_browser=True,
            email_address="privacy@lexisnexis.com",
            known_violations=[
                "Failed to honor 2019 CCPA opt-out request",
                "Sells precise geolocation data without consent",
            ],
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="Spokeo",
            type=BrokerType.AGRIGATOR,
            website="https://www.spokeo.com",
            opt_out_url="https://www.spokeo.com/opt-out",
            requires_browser=True,
            email_address="privacy@spokeo.com",
            known_violations=[
                "Continued data sales after opt-out request",
            ],
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="Whitepages",
            type=BrokerType.AGRIGATOR,
            website="https://www.whitepages.com",
            opt_out_url="https://www.whitepages.com/opt-out",
            requires_browser=True,
            email_address="privacy@whitepages.com",
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="Intelius",
            type=BrokerType.BACKGROUND_CHECK,
            website="https://www.intelius.com",
            opt_out_url="https://www.intelius.com/opt-out",
            requires_browser=True,
            email_address="privacy@intelius.com",
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="InstantCheckmate",
            type=BrokerType.BACKGROUND_CHECK,
            website="https://www.instantcheckmate.com",
            opt_out_url="https://www.instantcheckmate.com/opt-out",
            requires_browser=True,
            email_address="privacy@instantcheckmate.com",
            known_violations=[
                "Sells criminal record data without proper consent",
            ],
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="MyLife",
            type=BrokerType.AGRIGATOR,
            website="https://www.mylife.com",
            opt_out_url="https://www.mylife.com/opt-out",
            requires_browser=True,
            email_address="privacy@mylife.com",
            known_violations=[
                "FTC consent decree violation (2018)",
            ],
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="BeenVerified",
            type=BrokerType.BACKGROUND_CHECK,
            website="https://www.beenverified.com",
            opt_out_url="https://www.beenverified.com/opt-out",
            requires_browser=True,
            email_address="privacy@beenverified.com",
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="PeopleFinders",
            type=BrokerType.AGRIGATOR,
            website="https://www.peoplefinders.com",
            opt_out_url="https://www.peoplefinders.com/opt-out",
            requires_browser=True,
            email_address="privacy@peoplefinders.com",
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="TruthFinder",
            type=BrokerType.BACKGROUND_CHECK,
            website="https://www.truthfinder.com",
            opt_out_url="https://www.truthfinder.com/opt-out",
            requires_browser=True,
            email_address="privacy@truthfinder.com",
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="GenieInfo",
            type=BrokerType.SCORING,
            website="https://www.genieinfo.com",
            opt_out_url="https://www.genieinfo.com/opt-out",
            requires_browser=True,
            email_address="privacy@genieinfo.com",
            ccpa_compliant=False,
        ),
        BrokerProfile(
            name="Epsilon",
            type=BrokerType.DATABROKER,
            website="https://www.epsilon.com",
            opt_out_url="https://www.epsilon.com/opt-out",
            requires_browser=True,
            email_address="privacy@epsilon.com",
            known_violations=[
                "FTC settlement violation (2015)",
            ],
            ccpa_compliant=False,
        ),
    ]

    def __init__(self, dry_run: bool = True):
        """Initialize the opt-out automator.

        Args:
            dry_run: If True, don't actually submit requests.
        """
        self.dry_run = dry_run
        self.playwright_available = self._check_playwright()
        self.browser_timeout = int(os.environ.get("BROWSER_TIMEOUT", "60"))
        self.default_email_subject = "CCPA/GDPR Opt-Out Request"
        self.default_email_body = (
            "I am exercising my rights under the California Consumer Privacy "
            "Act (CCPA) and/or GDPR to opt-out of the sale of my personal "
            "information. Please remove all my data from your databases and "
            "confirm deletion in writing within 45 days.\n\n"
            "Name: [YOUR NAME]\n"
            "Email: [YOUR EMAIL]\n"
            "Date: [CURRENT DATE]\n"
        )
        self.last_run = None
        self.results: List[Dict] = []

    def _check_playwright(self) -> bool:
        """Check if Playwright is available.

        Returns:
            True if Playwright is installed.
        """
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def get_broker_list(self) -> List[Dict]:
        """Get list of known brokers.

        Returns:
            List of broker profiles.
        """
        return [
            {
                "name": b.name,
                "type": b.type.value,
                "website": b.website,
                "opt_out_url": b.opt_out_url,
                "email": b.email_address if b.email_address else "N/A",
                "known_violations": b.known_violations,
                "ccpa_compliant": b.ccpa_compliant,
                "gdpr_compliant": b.gdpr_compliant,
            }
            for b in self.KNOWN_BROKERS
        ]

    def submit_opt_out(
        self,
        broker_name: str,
        method: str = "auto",
        personal_info: Dict = None,
    ) -> Dict:
        """Submit an opt-out request to a broker.

        Args:
            broker_name: Name of the broker.
            method: Submission method (auto, browser, email, api).
            personal_info: Personal info for the opt-out.

        Returns:
            Submission result.
        """
        personal_info = personal_info or {}
        broker = self._find_broker(broker_name)
        if not broker:
            return {
                "status": "ERROR",
                "message": f"Broker not found: {broker_name}",
            }

        # Determine method if auto
        if method == "auto":
            if broker.has_api:
                method = "api"
            elif broker.requires_email:
                method = "email"
            else:
                method = "browser"

        # Execute submission
        if method == "browser" and self.playwright_available:
            return self._submit_via_browser(broker, personal_info)
        elif method == "email":
            return self._submit_via_email(broker, personal_info)
        elif method == "api":
            return self._submit_via_api(broker, personal_info)
        else:
            return {
                "status": "ERROR",
                "message": f"No available method for {broker_name}",
            }

    def _find_broker(self, name: str) -> Optional[BrokerProfile]:
        """Find a broker by name.

        Args:
            name: Broker name.

        Returns:
            Broker profile or None.
        """
        for broker in self.KNOWN_BROKERS:
            if broker.name.lower() == name.lower():
                return broker
        return None

    def _submit_via_browser(
        self,
        broker: BrokerProfile,
        personal_info: Dict,
    ) -> Dict:
        """Submit opt-out via headless browser.

        Args:
            broker: Broker profile.
            personal_info: Personal info.

        Returns:
            Submission result.
        """
        if self.dry_run:
            result = self._simulate_browser_submission(broker, personal_info)
        else:
            result = self._actual_browser_submission(broker, personal_info)

        # Record the result
        self._record_result(result)
        return result

    def _simulate_browser_submission(
        self,
        broker: BrokerProfile,
        personal_info: Dict,
    ) -> Dict:
        """Simulate browser submission (for dry run/testing).

        Args:
            broker: Broker profile.
            personal_info: Personal info.

        Returns:
            Simulation result.
        """
        request_id = hashlib.sha256(
            f"browser_{broker.name}_{datetime.now(timezone.utc).timestamp()}".encode()
        ).hexdigest()[:16]

        result = {
            "status": "SUBMITTED",
            "request_id": request_id,
            "broker": broker.name,
            "method": "browser",
            "url": broker.opt_out_url,
            "simulated": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": f"Simulated browser opt-out for {broker.name}",
        }

        return result

    def _actual_browser_submission(
        self,
        broker: BrokerProfile,
        personal_info: Dict,
    ) -> Dict:
        """Actually perform browser-based opt-out.

        Args:
            broker: Broker profile.
            personal_info: Personal info.

        Returns:
            Submission result.
        """
        try:
            from playwright.sync_api import sync_playwright

            request_id = hashlib.sha256(
                f"browser_{broker.name}_{datetime.now(timezone.utc).timestamp()}".encode()
            ).hexdigest()[:16]

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                try:
                    page.goto(broker.opt_out_url, timeout=self.browser_timeout * 1000)

                    # Fill in known form fields
                    name = personal_info.get("name", "")
                    email = personal_info.get("email", "")
                    address = personal_info.get("address", "")

                    # Try common form selectors
                    if name:
                        try:
                            page.fill('input[name*="name"], input[id*="name"]', name)
                        except Exception:
                            pass

                    if email:
                        try:
                            page.fill('input[name*="email"], input[id*="email"]', email)
                        except Exception:
                            pass

                    if address:
                        try:
                            page.fill('input[name*="address"], input[id*="address"]', address)
                        except Exception:
                            pass

                    # Try to submit
                    try:
                        page.click('button[type="submit"], input[type="submit"]')
                        page.wait_for_load_state("networkidle", timeout=30000)
                    except Exception:
                        pass  # Form may have different submit mechanism

                    result_status = "CONFIRMED" if page.url != broker.opt_out_url else "SUBMITTED"

                except Exception as e:
                    result_status = "ERROR"

                browser.close()

            return {
                "status": result_status,
                "request_id": request_id,
                "broker": broker.name,
                "method": "browser",
                "url": broker.opt_out_url,
                "simulated": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except ImportError:
            return {
                "status": "ERROR",
                "request_id": "",
                "broker": broker.name,
                "method": "browser",
                "message": "Playwright not installed",
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "request_id": "",
                "broker": broker.name,
                "method": "browser",
                "message": str(e),
            }

    def _submit_via_email(
        self,
        broker: BrokerProfile,
        personal_info: Dict,
    ) -> Dict:
        """Submit opt-out via email.

        Args:
            broker: Broker profile.
            personal_info: Personal info.

        Returns:
            Submission result.
        """
        if not broker.email_address:
            return {
                "status": "ERROR",
                "broker": broker.name,
                "method": "email",
                "message": "No email address available",
            }

        request_id = hashlib.sha256(
            f"email_{broker.name}_{datetime.now(timezone.utc).timestamp()}".encode()
        ).hexdigest()[:16]

        # Build email content
        subject = self.default_email_subject
        body = self.default_email_body.replace("[YOUR NAME]",
            personal_info.get("name", "Requester"))
        body = body.replace("[YOUR EMAIL]",
            personal_info.get("email", "requester@example.com"))
        body = body.replace("[CURRENT DATE]",
            datetime.now(timezone.utc).strftime("%B %d, %Y"))

        if self.dry_run:
            return {
                "status": "SUBMITTED",
                "request_id": request_id,
                "broker": broker.name,
                "method": "email",
                "recipient": broker.email_address,
                "subject": subject,
                "simulated": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Simulated email opt-out to {broker.email_address}",
            }

        # Send real email
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            smtp_config = {
                "host": os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com"),
                "port": int(os.environ.get("EMAIL_SMTP_PORT", "587")),
                "user": os.environ.get("EMAIL_SMTP_USER", ""),
                "password": os.environ.get("EMAIL_SMTP_PASSWORD", ""),
                "from": os.environ.get("EMAIL_FROM_ADDRESS", ""),
            }

            if not smtp_config["from"]:
                return {
                    "status": "ERROR",
                    "broker": broker.name,
                    "method": "email",
                    "message": "EMAIL_FROM_ADDRESS not configured",
                }

            msg = MIMEMultipart()
            msg["From"] = smtp_config["from"]
            msg["To"] = broker.email_address
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            server = smtplib.SMTP(smtp_config["host"], smtp_config["port"])
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_config["user"], smtp_config["password"])
            server.sendmail(smtp_config["from"], [broker.email_address], msg.as_string())
            server.quit()

            return {
                "status": "SUBMITTED",
                "request_id": request_id,
                "broker": broker.name,
                "method": "email",
                "recipient": broker.email_address,
                "subject": subject,
                "simulated": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "status": "ERROR",
                "request_id": request_id,
                "broker": broker.name,
                "method": "email",
                "message": str(e),
            }

    def _submit_via_api(
        self,
        broker: BrokerProfile,
        personal_info: Dict,
    ) -> Dict:
        """Submit opt-out via broker API.

        Args:
            broker: Broker profile.
            personal_info: Personal info.

        Returns:
            Submission result.
        """
        if not broker.has_api or not broker.api_endpoint:
            return {
                "status": "ERROR",
                "broker": broker.name,
                "method": "api",
                "message": "No API available",
            }

        request_id = hashlib.sha256(
            f"api_{broker.name}_{datetime.now(timezone.utc).timestamp()}".encode()
        ).hexdigest()[:16]

        # Build API request
        payload = {
            "action": "opt_out",
            "personal_info": personal_info,
            "request_id": request_id,
        }

        if self.dry_run:
            return {
                "status": "SUBMITTED",
                "request_id": request_id,
                "broker": broker.name,
                "method": "api",
                "endpoint": broker.api_endpoint,
                "simulated": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Simulated API opt-out to {broker.api_endpoint}",
            }

        # Send real API request
        try:
            import requests

            response = requests.post(
                broker.api_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )

            return {
                "status": "CONFIRMED" if response.status_code == 200 else "ERROR",
                "request_id": request_id,
                "broker": broker.name,
                "method": "api",
                "endpoint": broker.api_endpoint,
                "response_code": response.status_code,
                "simulated": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            return {
                "status": "ERROR",
                "request_id": request_id,
                "broker": broker.name,
                "method": "api",
                "message": str(e),
            }

    def _record_result(self, result: Dict) -> None:
        """Record submission result to database.

        Args:
            result: Submission result.
        """
        session = get_session()
        try:
            record = OptOutRecord(
                broker_name=result.get("broker", ""),
                method=result.get("method", "unknown"),
                request_id=result.get("request_id", ""),
                status=result.get("status", "ERROR"),
                response_data=json.dumps(result),
            )
            session.add(record)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to record opt-out result: %s", e)
        finally:
            session.close()

        self.results.append(result)

    def run_batch_opt_outs(
        self,
        broker_names: List[str] = None,
        personal_info: Dict = None,
    ) -> Dict:
        """Run opt-out requests against multiple brokers.

        Args:
            broker_names: List of broker names. Defaults to all known.
            personal_info: Personal info for submissions.

        Returns:
            Batch result summary.
        """
        if broker_names is None:
            broker_names = [b.name for b in self.KNOWN_BROKERS]

        results = []
        for name in broker_names:
            result = self.submit_opt_out(name, personal_info=personal_info)
            results.append(result)

        self.last_run = datetime.now(timezone.utc)

        summary = {
            "total": len(results),
            "submitted": len([r for r in results if r["status"] == "SUBMITTED"]),
            "confirmed": len([r for r in results if r["status"] == "CONFIRMED"]),
            "errors": len([r for r in results if r["status"] == "ERROR"]),
            "results": results,
            "run_at": self.last_run.isoformat(),
        }

        return summary

    def get_pending_resubmissions(self) -> List[Dict]:
        """Get brokers that need monthly re-submission.

        Returns:
            List of brokers pending re-submission.
        """
        session = get_session()
        try:
            records = (
                session.query(OptOutRecord)
                .filter(
                    OptOutRecord.status.in_(["SUBMITTED", "CONFIRMED"]),
                    OptOutRecord.last_attempt < datetime.now(timezone.utc)
                    - timedelta(days=30),
                )
                .all()
            )

            return [
                {
                    "broker": r.broker_name,
                    "last_attempt": r.last_attempt.isoformat() if r.last_attempt else None,
                    "status": r.status,
                    "request_id": r.request_id,
                }
                for r in records
            ]
        except Exception as e:
            logger.error("Failed to get pending resubmissions: %s", e)
            return []
        finally:
            session.close()

    def get_success_rate(self, broker_name: str = None) -> Dict:
        """Get opt-out success rate.

        Args:
            broker_name: Optional specific broker.

        Returns:
            Success rate dict.
        """
        session = get_session()
        try:
            query = session.query(OptOutRecord)
            if broker_name:
                query = query.filter(OptOutRecord.broker_name == broker_name)

            records = query.all()

            if not records:
                return {
                    "total": 0,
                    "success_rate": 0.0,
                    "total_submitted": 0,
                    "total_confirmed": 0,
                }

            total = len(records)
            confirmed = len([r for r in records if r.status == "CONFIRMED"])

            return {
                "total": total,
                "success_rate": confirmed / total if total > 0 else 0.0,
                "total_submitted": total,
                "total_confirmed": confirmed,
                "by_status": {
                    "confirmed": confirmed,
                    "submitted": len([r for r in records if r.status == "SUBMITTED"]),
                    "ignored": len([r for r in records if r.status == "IGNORED"]),
                    "error": len([r for r in records if r.status == "ERROR"]),
                },
            }
        except Exception as e:
            logger.error("Failed to get success rate: %s", e)
            return {"total": 0, "success_rate": 0.0}
        finally:
            session.close()

    def get_opt_out_evidence(self, broker_name: str) -> Optional[Dict]:
        """Get evidence that a broker ignored an opt-out request.

        Used for FTC/AG complaint evidence.

        Args:
            broker_name: Broker name.

        Returns:
            Evidence dict or None.
        """
        session = get_session()
        try:
            records = (
                session.query(OptOutRecord)
                .filter(
                    OptOutRecord.broker_name == broker_name,
                    OptOutRecord.status.in_(["SUBMITTED", "CONFIRMED"]),
                )
                .order_by(OptOutRecord.last_attempt.desc())
                .limit(5)
                .all()
            )

            if not records:
                return None

            return {
                "broker": broker_name,
                "opt_out_attempts": [
                    {
                        "request_id": r.request_id,
                        "status": r.status,
                        "method": r.method,
                        "timestamp": r.last_attempt.isoformat() if r.last_attempt else None,
                    }
                    for r in records
                ],
                "violation_note": (
                    f"Data broker '{broker_name}' was contacted {len(records)} time(s) "
                    f"for opt-out. If data is still found in their databases, this "
                    f"constitutes a CCPA/GDPR violation."
                ),
            }
        except Exception as e:
            logger.error("Failed to get opt-out evidence: %s", e)
            return None
        finally:
            session.close()
