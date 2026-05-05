"""Arbitration Drafting for Omnitrader Legal Module.

Generates legal documents including:
- CCPA demand letters
- Small claims complaints
- FTC/AG whistleblower narratives
- Arbitration claims

All documents are templates that require human review.
This module does NOT constitute legal advice.
"""

import os
import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pathlib import Path

from ..utils.logging_config import get_logger
from ..utils.db import get_session, DataBrokerAlert, SystemEvent

logger = get_logger("legal.arbitration")


class DocumentTemplate:
    """Base class for legal document templates."""

    name: str = "base"
    description: str = ""

    def render(self, context: Dict) -> str:
        """Render the template with given context.

        Args:
            context: Template variables.

        Returns:
            Rendered document text.
        """
        raise NotImplementedError


class CCPADemandLetter(DocumentTemplate):
    """CCPA (California Consumer Privacy Act) demand letter template.

    Used when data brokers fail to honor opt-out requests or
    are found selling personal data without consent.
    """

    name = "ccpa_demand_letter"
    description = "CCPA demand letter for data privacy violations"

    def render(self, context: Dict) -> str:
        """Render a CCPA demand letter.

        Args:
            context: Dict containing:
                - broker_name: Name of the data broker
                - broker_address: Address of the data broker
                - violation_type: Type of violation
                - evidence_summary: Summary of evidence
                - demand_amount: Statutory damages amount
                - deadline_days: Days to comply (default 30)
                - claimant_name: Your name/address
                - claimant_email: Your email

        Returns:
            Rendered demand letter text.
        """
        broker_name = context.get("broker_name", "UNKNOWN")
        broker_address = context.get("broker_address", "UNKNOWN")
        violation_type = context.get("violation_type", "data_sale")
        evidence = context.get("evidence_summary", "")
        demand = context.get("demand_amount", 750)
        deadline = context.get("deadline_days", 30)
        claimant = context.get("claimant_name", "[YOUR NAME]")
        claimant_addr = context.get("claimant_address", "[YOUR ADDRESS]")
        claimant_email = context.get("claimant_email", "[YOUR EMAIL]")

        today = datetime.now(timezone.utc).strftime("%B %d, %Y")
        deadline_date = (datetime.now(timezone.utc).replace(
            day=datetime.now(timezone.utc).day + deadline
        )).strftime("%B %d, %Y")

        template = f"""
DEMAND LETTER — CCPA VIOLATION

Date: {today}

TO:
{broker_name}
{broker_address}

RE: Demand for Compliance Under California Consumer Privacy Act (CCPA)

Dear {broker_name},

I am writing to formally notify you of violations of the California Consumer
Privacy Act (Cal. Civ. Code §§ 1798.100-1798.199) and to demand immediate
cure of the identified violations.

VIOLATION SUMMARY
{'=' * 60}

{violation_type.upper()} — {evidence}

Under CCPA § 1798.120, businesses are required to:
1. Provide notice at collection about categories of personal information
2. Provide opt-out rights for sale of personal information
3. Honor opt-out requests within 15 business days
4. Maintain reasonable security procedures

Your failure to comply with these obligations constitutes a violation
entitling me to statutory damages of $100-$750 per violation per incident
(CCPA § 1798.120(a)(3)).

DEMAND
{'=' * 60}

I hereby demand that you:

1. Immediately cease all unauthorized collection and sale of my personal
   information, including but not limited to: {violation_type}

2. Delete all personal information about me from your databases and
   disclose all third parties to whom such information has been sold

3. Provide written certification of compliance within {deadline} days
   (by {deadline_date})

4. Pay statutory damages in the amount of ${demand} for the identified
   violations

If you fail to comply with this demand within {deadline} days, I will
pursue all available legal remedies, including filing a complaint with
the California Attorney General and initiating civil litigation for
statutory damages, injunctive relief, and attorney's fees.

RESERVATION OF RIGHTS
{'=' * 60}

This demand letter does not constitute a waiver of any rights or remedies
available under California law or federal law. All rights are expressly
reserved.

Please direct all communications regarding this matter to:

{claimant_name}
{claimant_addr}
{claimant_email}

Sincerely,

{claimant_name}
"""
        return template.strip()


class SmallClaimsComplaint(DocumentTemplate):
    """Small claims complaint template for data broker violations.

    Used when statutory damages are sought through small claims court.
    """

    name = "small_claims_complaint"
    description = "Small claims complaint for data broker violations"

    def render(self, context: Dict) -> str:
        """Render a small claims complaint.

        Args:
            context: Dict containing complaint details.

        Returns:
            Rendered complaint text.
        """
        defendant = context.get("defendant_name", "UNKNOWN")
        defendant_address = context.get("defendant_address", "UNKNOWN")
        plaintiff = context.get("plaintiff_name", "[YOUR NAME]")
        plaintiff_address = context.get("plaintiff_address", "[YOUR ADDRESS]")
        claim_amount = context.get("claim_amount", 10000)
        facts = context.get("facts", "")
        court = context.get("court", "Superior Court of California")
        county = context.get("county", "[COUNTY]")

        today = datetime.now(timezone.utc).strftime("%B %d, %Y")

        template = f"""
SMALL CLAIMS COMPLAINT

Court: {court}
County: {county}

PLAINTIFF:
{plaintiff}
{plaintiff_address}

DEFENDANT:
{defendant}
{defendant_address}

CLAIM AMOUNT: ${claim_amount:,.2f}

COMPLAINT
{'=' * 60}

1. PLAINTIFF {plaintiff}, an individual residing at {plaintiff_address},
   files this complaint against DEFENDANT {defendant}.

2. JURISDICTION: This Court has jurisdiction over this matter because
   the amount in controversy does not exceed ${claim_amount:,.2f}
   and Defendant conducts business within this county.

3. FACTS: {facts}

4. CAUSES OF ACTION:

   a) Violation of California Consumer Privacy Act (CCPA)
      Cal. Civ. Code §§ 1798.100-1798.199

   b) Unfair Business Practices
      Cal. Bus. & Prof. Code § 17200

   c) Violation of Consumer Legal Remedies Act
      Cal. Civ. Code §§ 1750-1770

5. DAMAGES: Plaintiff has suffered actual and statutory damages
   in the amount of ${claim_amount:,.2f}.

WHEREFORE, Plaintiff prays for judgment against Defendant as follows:
   a) Compensatory damages in the amount of ${claim_amount:,.2f}
   b) Statutory damages as provided by law
   c) Pre-judgment interest
   d) Costs of suit

Date: {today}

{plaintiff}
Plaintiff, Pro Se
"""
        return template.strip()


class FTCWhistleblowerNarrative(DocumentTemplate):
    """FTC/State Attorney General whistleblower narrative template.

    Used when submitting evidence of data broker violations to
    the FTC, state attorneys general, or other regulatory bodies.
    """

    name = "ftc_whistleblower_narrative"
    description = "FTC/AG whistleblower narrative for data violations"

    def render(self, context: Dict) -> str:
        """Render an FTC whistleblower narrative.

        Args:
            context: Dict containing incident details.

        Returns:
            Rendered narrative text.
        """
        subject = context.get("subject", "UNKNOWN")
        subject_type = context.get("subject_type", "data_broker")
        violations = context.get("violations", [])
        evidence_summary = context.get("evidence_summary", "")
        impact_description = context.get("impact_description", "")
        reporter_info = context.get("reporter_info", {})

        today = datetime.now(timezone.utc).strftime("%B %d, %Y")

        template = f"""
WHISTLEBLOWER REPORT — DATA PRIVACY VIOLATIONS

Submitted: {today}
Report Type: Consumer Protection Violation

SUBJECT
{'=' * 60}

Subject: {subject}
Type: {subject_type}

NARRATIVE
{'=' * 60}

This report documents violations of consumer protection laws by
{subject}, a {subject_type} operating in the United States.

IDENTIFIED VIOLATIONS
{'=' * 60}

"""
        for i, violation in enumerate(violations, 1):
            template += f"{i}. {violation}\n"

        template += f"""
EVIDENCE SUMMARY
{'=' * 60}

{evidence_summary}

IMPACT
{'=' * 60}

{impact_description if impact_description else "No specific impact data provided."}

CONCLUSION
{'=' * 60}

Based on the evidence presented, {subject} appears to have engaged in
systematic violations of consumer privacy laws. This report is submitted
in the public interest to support regulatory enforcement actions.

REPORTER INFORMATION
{'=' * 60}

Reporter: {reporter_info.get('name', 'Anonymous')}
Contact: {reporter_info.get('contact', 'N/A')}
Affiliation: {reporter_info.get('affiliation', 'Independent')}
"""
        return template.strip()


class LegalDraftingEngine:
    """Generates legal documents using templates and LLM assistance.

    This engine orchestrates document generation, incorporating
    both template-based and LLM-enhanced drafting.
    """

    def __init__(self, llm_enabled: bool = True):
        """Initialize the legal drafting engine.

        Args:
            llm_enabled: Whether to use LLM for enhanced drafting.
        """
        self.templates = {
            "ccpa_demand": CCPADemandLetter(),
            "small_claims": SmallClaimsComplaint(),
            "ftc_narrative": FTCWhistleblowerNarrative(),
        }
        self.llm_enabled = llm_enabled
        self.output_dir = Path(
            os.environ.get(
                "LEGAL_OUTPUT_DIR",
                "/var/log/omnitrader/legal",
            )
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def draft_document(
        self,
        template_name: str,
        context: Dict,
        human_review_required: bool = True,
    ) -> Dict:
        """Draft a legal document.

        Args:
            template_name: Template to use.
            context: Template variables.
            human_review_required: If True, flag for human review.

        Returns:
            Draft result dict.
        """
        template = self.templates.get(template_name)
        if not template:
            available = ", ".join(self.templates.keys())
            raise ValueError(f"Unknown template: {template_name}. Available: {available}")

        # Generate the document
        document_text = template.render(context)

        # Save the draft
        doc_id = f"{template_name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        output_path = self.output_dir / f"{doc_id}.txt"
        with open(output_path, "w") as f:
            f.write(document_text)

        # Log the event
        self._log_draft_event(doc_id, template_name, str(output_path), human_review_required)

        return {
            "doc_id": doc_id,
            "template": template_name,
            "path": str(output_path),
            "text": document_text,
            "human_review_required": human_review_required,
            "status": "DRAFTED" if human_review_required else "FINAL",
        }

    def _log_draft_event(
        self,
        doc_id: str,
        template_name: str,
        path: str,
        human_review: bool,
    ) -> None:
        """Log a document draft event to the database.

        Args:
            doc_id: Document ID.
            template_name: Template used.
            path: File path of the draft.
            human_review: Whether human review is required.
        """
        session = get_session()
        try:
            event = SystemEvent(
                event_type="legal_draft",
                message=f"Drafted {template_name} as {doc_id}",
            )
            session.add(event)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to log legal draft: %s", e)
        finally:
            session.close()

    async def llm_enhance_draft(
        self,
        template_name: str,
        context: Dict,
        prompt_override: str = None,
    ) -> Dict:
        """Use LLM to enhance a drafted document.

        Args:
            template_name: Base template.
            context: Template variables.
            prompt_override: Custom LLM prompt.

        Returns:
            Enhanced document dict.
        """
        if not self.llm_enabled:
            return self.draft_document(template_name, context)

        try:
            from ..intelligence.llm_interface import LLMInterface

            llm = LLMInterface.load()

            base_doc = self.draft_document(template_name, context)

            prompt = prompt_override or (
                f"Enhance this legal document draft. Improve clarity, "
                f"legal precision, and persuasive language. Keep all "
                f"factual content unchanged. Do not add speculative claims.\n\n"
                f"---\n{base_doc['text']}\n---\n"
                f"Provide the enhanced version:"
            )

            enhanced_text = llm.call_llm(prompt)

            doc_id = f"{template_name}_enhanced_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            output_path = self.output_dir / f"{doc_id}.txt"
            with open(output_path, "w") as f:
                f.write(enhanced_text)

            return {
                "doc_id": doc_id,
                "template": template_name,
                "path": str(output_path),
                "text": enhanced_text,
                "human_review_required": True,
                "status": "ENHANCED_DRAFT",
            }

        except Exception as e:
            logger.error("LLM enhancement failed: %s", e)
            return self.draft_document(template_name, context)

    def generate_ccpa_demand(
        self,
        broker_name: str,
        broker_address: str,
        violation_type: str,
        evidence_summary: str,
        demand_amount: float = 750.0,
        deadline_days: int = 30,
        claimant_name: str = "[YOUR NAME]",
        claimant_address: str = "[YOUR ADDRESS]",
        claimant_email: str = "[YOUR EMAIL]",
    ) -> Dict:
        """Generate a CCPA demand letter.

        Args:
            broker_name: Name of the data broker.
            broker_address: Address of the data broker.
            violation_type: Type of CCPA violation.
            evidence_summary: Summary of evidence.
            demand_amount: Statutory demand amount.
            deadline_days: Days to respond.
            claimant_name: Claimant name.
            claimant_address: Claimant address.
            claimant_email: Claimant email.

        Returns:
            Draft result dict.
        """
        context = {
            "broker_name": broker_name,
            "broker_address": broker_address,
            "violation_type": violation_type,
            "evidence_summary": evidence_summary,
            "demand_amount": demand_amount,
            "deadline_days": deadline_days,
            "claimant_name": claimant_name,
            "claimant_address": claimant_address,
            "claimant_email": claimant_email,
        }
        return self.draft_document("ccpa_demand", context)

    def generate_small_claims(
        self,
        defendant_name: str,
        defendant_address: str,
        plaintiff_name: str,
        plaintiff_address: str,
        claim_amount: float,
        facts: str,
        court: str = "Superior Court of California",
        county: str = "[COUNTY]",
    ) -> Dict:
        """Generate a small claims complaint.

        Args:
            defendant_name: Defendant name.
            defendant_address: Defendant address.
            plaintiff_name: Plaintiff name.
            plaintiff_address: Plaintiff address.
            claim_amount: Claim amount.
            facts: Statement of facts.
            court: Court name.
            county: County.

        Returns:
            Draft result dict.
        """
        context = {
            "defendant_name": defendant_name,
            "defendant_address": defendant_address,
            "plaintiff_name": plaintiff_name,
            "plaintiff_address": plaintiff_address,
            "claim_amount": claim_amount,
            "facts": facts,
            "court": court,
            "county": county,
        }
        return self.draft_document("small_claims", context)

    def generate_ftc_narrative(
        self,
        subject: str,
        subject_type: str,
        violations: List[str],
        evidence_summary: str,
        impact_description: str = "",
        reporter_info: Dict = None,
    ) -> Dict:
        """Generate an FTC whistleblower narrative.

        Args:
            subject: Subject of the report.
            subject_type: Type of entity.
            violations: List of violations.
            evidence_summary: Evidence summary.
            impact_description: Impact description.
            reporter_info: Reporter information.

        Returns:
            Draft result dict.
        """
        context = {
            "subject": subject,
            "subject_type": subject_type,
            "violations": violations,
            "evidence_summary": evidence_summary,
            "impact_description": impact_description,
            "reporter_info": reporter_info or {},
        }
        return self.draft_document("ftc_narrative", context)

    def get_available_templates(self) -> List[Dict]:
        """Get list of available document templates.

        Returns:
            List of template info dicts.
        """
        return [
            {
                "name": k,
                "description": t.description,
            }
            for k, t in self.templates.items()
        ]
