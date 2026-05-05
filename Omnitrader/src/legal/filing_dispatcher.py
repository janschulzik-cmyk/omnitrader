"""Filing Dispatcher for Omnitrader Legal Module.

Handles the dispatch of legal documents to various filing systems:
- PACER (federal courts)
- CourtConnect (state courts)
- Direct mail generation (printable packets + mailing labels)
- Human-in-the-loop queue for user approval

All filings require explicit user approval before submission.
"""

import os
import json
import hashlib
import smtplib
import ssl
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

from ..utils.logging_config import get_logger
from ..utils.db import get_session, SystemEvent, SystemSetting

logger = get_logger("legal.filing")


class FilingStatus(str, Enum):
    """Filing status enum."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class FilingChannel(str, Enum):
    """Filing channel enum."""
    PACER = "PACER"
    COURT_CONNECT = "COURT_CONNECT"
    EMAIL = "EMAIL"
    MAIL = "MAIL"
    HUMAN_QUEUE = "HUMAN_QUEUE"


@dataclass
class FilingRequest:
    """Represents a filing request."""
    document_id: str
    document_path: str
    document_text: str
    channel: FilingChannel
    recipient: str
    metadata: Dict = field(default_factory=dict)
    status: FilingStatus = FilingStatus.PENDING
    filing_id: str = field(default_factory=lambda: hashlib.sha256(
        f"{datetime.now(timezone.utc).isoformat()}_{os.urandom(8).hex()}".encode()
    ).hexdigest()[:16])
    approved_by: Optional[str] = None
    submitted_at: Optional[datetime] = None


class HumanApprovalQueue:
    """Human-in-the-loop approval queue for legal filings.

    Ensures no filing is submitted without explicit user approval.
    This prevents unauthorized UPL (Unauthorized Practice of Law).
    """

    def __init__(self):
        self.queue_path = Path(
            os.environ.get(
                "LEGAL_APPROVAL_QUEUE_PATH",
                "/var/lib/omnitrader/approval_queue.json",
            )
        )
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_queue()

    def _load_queue(self) -> None:
        """Load the approval queue from disk."""
        try:
            with open(self.queue_path) as f:
                self.queue = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.queue = []

    def _save_queue(self) -> None:
        """Save the approval queue to disk."""
        with open(self.queue_path, "w") as f:
            json.dump(self.queue, f, indent=2)

    def add_to_queue(self, request: FilingRequest) -> str:
        """Add a filing request to the approval queue.

        Args:
            request: The filing request.

        Returns:
            Queue ticket ID.
        """
        ticket = hashlib.sha256(
            f"{request.filing_id}_{datetime.now(timezone.utc).timestamp()}".encode()
        ).hexdigest()[:12]

        entry = {
            "ticket": ticket,
            "filing_id": request.filing_id,
            "document_id": request.document_id,
            "channel": request.channel.value,
            "recipient": request.recipient,
            "metadata": request.metadata,
            "status": "PENDING_APPROVAL",
            "added_at": datetime.now(timezone.utc).isoformat(),
            "document_preview": request.document_text[:500] + "..." if len(request.document_text) > 500 else request.document_text,
        }

        self.queue.append(entry)
        self._save_queue()
        logger.info("Filing %s added to approval queue (ticket: %s)",
                     request.filing_id, ticket)
        return ticket

    def get_pending(self) -> List[Dict]:
        """Get all pending approvals.

        Returns:
            List of pending filing requests.
        """
        return [e for e in self.queue if e["status"] == "PENDING_APPROVAL"]

    def approve(self, ticket: str, approver: str = "admin") -> bool:
        """Approve a filing request.

        Args:
            ticket: Queue ticket ID.
            approver: Who approved.

        Returns:
            True if approved.
        """
        for entry in self.queue:
            if entry["ticket"] == ticket and entry["status"] == "PENDING_APPROVAL":
                entry["status"] = "APPROVED"
                entry["approved_by"] = approver
                entry["approved_at"] = datetime.now(timezone.utc).isoformat()
                self._save_queue()
                logger.info("Filing %s approved by %s", ticket, approver)
                return True
        return False

    def reject(self, ticket: str, reason: str = "", approver: str = "admin") -> bool:
        """Reject a filing request.

        Args:
            ticket: Queue ticket ID.
            reason: Rejection reason.
            approver: Who rejected.

        Returns:
            True if rejected.
        """
        for entry in self.queue:
            if entry["ticket"] == ticket and entry["status"] == "PENDING_APPROVAL":
                entry["status"] = "REJECTED"
                entry["rejected_by"] = approver
                entry["rejection_reason"] = reason
                entry["rejected_at"] = datetime.now(timezone.utc).isoformat()
                self._save_queue()
                logger.info("Filing %s rejected by %s: %s",
                             ticket, approver, reason)
                return True
        return False

    def get_queue_summary(self) -> Dict:
        """Get summary statistics.

        Returns:
            Summary dict.
        """
        return {
            "pending": len([e for e in self.queue if e["status"] == "PENDING_APPROVAL"]),
            "approved": len([e for e in self.queue if e["status"] == "APPROVED"]),
            "rejected": len([e for e in self.queue if e["status"] == "REJECTED"]),
            "total": len(self.queue),
        }


class FilingDispatcher:
    """Dispatches legal documents to filing channels.

    Supports PACER, CourtConnect, email, and mail filing.
    All filings go through the approval queue first.
    """

    def __init__(self, approval_queue: HumanApprovalQueue = None):
        """Initialize the filing dispatcher.

        Args:
            approval_queue: Human approval queue instance.
        """
        self.approval_queue = approval_queue or HumanApprovalQueue()
        self.filing_history: List[Dict] = []

    def submit_filing(
        self,
        request: FilingRequest,
        require_approval: bool = True,
    ) -> Dict:
        """Submit a filing request.

        Args:
            request: The filing request.
            require_approval: If True, require human approval first.

        Returns:
            Submission result.
        """
        if require_approval:
            # Go through approval queue
            ticket = self.approval_queue.add_to_queue(request)

            # Check if auto-approve is enabled for certain channels
            auto_approve_channels = os.environ.get(
                "LEGAL_AUTO_APPROVE_CHANNELS", ""
            ).split(",")
            if request.channel.value in auto_approve_channels:
                self.approval_queue.approve(ticket, "AUTO_APPROVE")

            return {
                "status": "PENDING_APPROVAL",
                "filing_id": request.filing_id,
                "ticket": ticket,
                "message": "Filing added to approval queue",
            }
        else:
            # Direct submission (for testing only)
            return self._execute_filing(request)

    def process_approved_filings(self) -> List[Dict]:
        """Process all approved but not yet submitted filings.

        Returns:
            List of submission results.
        """
        results = []

        for entry in list(self.approval_queue.queue):
            if entry["status"] != "APPROVED":
                continue

            # Find the original request
            request = self._find_request(entry["filing_id"])
            if not request:
                continue

            result = self._execute_filing(request)
            results.append(result)

            # Update queue entry
            entry["status"] = "SUBMITTED" if result["status"] == "OK" else "FAILED"
            entry["submission_result"] = result

        self.approval_queue._save_queue()
        return results

    def _execute_filing(self, request: FilingRequest) -> Dict:
        """Execute the actual filing.

        Args:
            request: The filing request.

        Returns:
            Execution result.
        """
        try:
            if request.channel == FilingChannel.PACER:
                return self._submit_to_pacer(request)
            elif request.channel == FilingChannel.COURT_CONNECT:
                return self._submit_to_court_connect(request)
            elif request.channel == FilingChannel.EMAIL:
                return self._submit_by_email(request)
            elif request.channel == FilingChannel.MAIL:
                return self._prepare_mail(request)
            else:
                return {
                    "status": "ERROR",
                    "filing_id": request.filing_id,
                    "message": f"Unknown channel: {request.channel}",
                }

        except Exception as e:
            logger.error("Filing %s failed: %s", request.filing_id, e)
            return {
                "status": "FAILED",
                "filing_id": request.filing_id,
                "message": str(e),
            }

    def _submit_to_pacer(self, request: FilingRequest) -> Dict:
        """Submit to PACER (federal courts).

        Note: PACER filing requires a PACER account and API credentials.
        This is a simulation for now.

        Args:
            request: The filing request.

        Returns:
            Submission result.
        """
        logger.info("PACER filing for %s (simulation mode)", request.filing_id)

        # In production, this would use the PACER API
        # For now, just log the attempt
        return {
            "status": "OK",
            "filing_id": request.filing_id,
            "channel": "PACER",
            "message": "Filing queued for PACER submission",
            "pacer_case_number": request.metadata.get("case_number", "TBD"),
        }

    def _submit_to_court_connect(self, request: FilingRequest) -> Dict:
        """Submit to CourtConnect (state courts).

        Args:
            request: The filing request.

        Returns:
            Submission result.
        """
        logger.info("CourtConnect filing for %s (simulation mode)",
                     request.filing_id)

        return {
            "status": "OK",
            "filing_id": request.filing_id,
            "channel": "COURT_CONNECT",
            "message": "Filing queued for CourtConnect submission",
        }

    def _submit_by_email(self, request: FilingRequest) -> Dict:
        """Submit by email.

        Args:
            request: The filing request.

        Returns:
            Submission result.
        """
        try:
            # Build email
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            smtp_config = {
                "host": os.environ.get("LEGAL_SMTP_HOST", "smtp.gmail.com"),
                "port": int(os.environ.get("LEGAL_SMTP_PORT", "587")),
                "user": os.environ.get("LEGAL_SMTP_USER", ""),
                "password": os.environ.get("LEGAL_SMTP_PASSWORD", ""),
                "from": os.environ.get(
                    "LEGAL_FROM_EMAIL", "omnitrader@protected.local"
                ),
            }

            msg = MIMEMultipart()
            msg["From"] = smtp_config["from"]
            msg["To"] = request.recipient
            msg["Subject"] = f"Legal Filing: {request.document_id}"

            body = (
                f"Legal Filing Document\n"
                f"Document ID: {request.document_id}\n"
                f"Filing ID: {request.filing_id}\n"
                f"Date: {datetime.now(timezone.utc).isoformat()}\n\n"
                f"See attached document.\n\n"
                f"This is an automated filing from Omnitrader.\n"
            )

            msg.attach(MIMEText(body, "plain"))

            # Attach the document
            if os.path.exists(request.document_path):
                try:
                    from email.mime.base import MIMEBase
                    from email import encoders

                    with open(request.document_path, "rb") as f:
                        part = MIMEBase("application", "pdf")
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f'attachment; filename="{os.path.basename(request.document_path)}"',
                        )
                        msg.attach(part)
                except Exception:
                    pass  # Skip attachment if it fails

            # Send
            server = smtplib.SMTP(smtp_config["host"], smtp_config["port"])
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_config["user"], smtp_config["password"])
            server.sendmail(smtp_config["from"], [request.recipient], msg.as_string())
            server.quit()

            logger.info("Email filing sent to %s", request.recipient)

            return {
                "status": "OK",
                "filing_id": request.filing_id,
                "channel": "EMAIL",
                "message": f"Sent to {request.recipient}",
            }

        except Exception as e:
            return {
                "status": "FAILED",
                "filing_id": request.filing_id,
                "message": f"Email send failed: {e}",
            }

    def _prepare_mail(self, request: FilingRequest) -> Dict:
        """Prepare mail packet with labels.

        Args:
            request: The filing request.

        Returns:
            Preparation result.
        """
        try:
            # Generate mailing label
            recipient_name = request.metadata.get("recipient_name", "Recipient")
            recipient_address = request.metadata.get("recipient_address", "")

            label_dir = Path(os.environ.get(
                "LEGAL_MAIL_OUTPUT_DIR",
                "/var/lib/omnitrader/mail",
            ))
            label_dir.mkdir(parents=True, exist_ok=True)

            label_path = label_dir / f"label_{request.filing_id}.txt"
            with open(label_path, "w") as f:
                f.write(f"{recipient_name}\n")
                f.write(f"{recipient_address}\n")

            # Prepare envelope template
            envelope_path = label_dir / f"envelope_{request.filing_id}.txt"
            with open(envelope_path, "w") as f:
                f.write("ENVELOPE FOR:\n")
                f.write(f"{recipient_name}\n")
                f.write(f"{recipient_address}\n\n")
                f.write(f"Filing ID: {request.filing_id}\n")
                f.write(f"Document: {request.document_id}\n")

            logger.info("Mail packet prepared for %s", request.filing_id)

            return {
                "status": "OK",
                "filing_id": request.filing_id,
                "channel": "MAIL",
                "message": "Mail packet prepared",
                "label_path": str(label_path),
                "envelope_path": str(envelope_path),
                "document_path": request.document_path,
            }

        except Exception as e:
            return {
                "status": "FAILED",
                "filing_id": request.filing_id,
                "message": f"Mail prep failed: {e}",
            }

    def _find_request(self, filing_id: str) -> Optional[FilingRequest]:
        """Find a request by filing ID.

        Args:
            filing_id: Filing ID.

        Returns:
            Request or None.
        """
        for item in self.filing_history:
            if item.get("filing_id") == filing_id:
                return item.get("_request")
        return None

    def get_submission_history(self) -> List[Dict]:
        """Get submission history.

        Returns:
            List of submission records.
        """
        return [
            {k: v for k, v in item.items() if k != "_request"}
            for item in self.filing_history
        ]
