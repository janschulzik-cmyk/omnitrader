"""DAO Governance for Omnitrader WatchdogDAO.

Manages:
- Proposal creation and voting
- Quorum calculation
- Proposal execution
- Governance parameter updates
"""

import os
import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set
from enum import Enum
from dataclasses import dataclass, field

from ..utils.logging_config import get_logger
from ..utils.db import get_session, SystemEvent

logger = get_logger("dao.governance")


class ProposalType(str, Enum):
    """Types of governance proposals."""
    FUND_ALLOCATION = "fund_allocation"
    TARGET_SELECTION = "target_selection"
    CODE_UPGRADE = "code_upgrade"
    PARAM_UPDATE = "param_update"


@dataclass
class GovernanceVote:
    """Represents a governance vote."""
    voter: str
    proposal_id: str
    vote: str  # "for", "against", "abstain"
    weight: float  # Token weight
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tx_hash: str = ""


class GovernanceEngine:
    """Manages DAO governance — proposals, voting, and execution.

    Supports:
    - Multiple proposal types (fund allocation, target selection, code upgrade)
    - Token-weighted voting
    - Quorum requirements
    - Voting periods
    - Proposal execution on pass
    """

    def __init__(
        self,
        quorum_pct: float = 0.05,  # 5% of supply
        voting_period_hours: int = 168,  # 7 days
        threshold_pct: float = 0.5,  # 50% majority
        min_proposal_stake: float = 100.0,  # Minimum stake to propose
    ):
        """Initialize the governance engine.

        Args:
            quorum_pct: Percentage of supply needed for quorum.
            voting_period_hours: Voting period in hours.
            threshold_pct: Percentage needed to pass.
            min_proposal_stake: Minimum stake to create proposal.
        """
        self.quorum_pct = quorum_pct
        self.voting_period_hours = voting_period_hours
        self.threshold_pct = threshold_pct
        self.min_proposal_stake = min_proposal_stake

        # Active proposals
        self.proposals: Dict[str, Dict] = {}
        # All votes
        self.votes: List[GovernanceVote] = []
        # Total token supply (loaded from token manager)
        self.total_supply = 1_000_000  # Default

    def create_proposal(
        self,
        proposer: str,
        title: str,
        description: str,
        proposal_type: str,
        metadata: Dict = None,
    ) -> Dict:
        """Create a new governance proposal.

        Args:
            proposer: Address of proposer.
            title: Proposal title.
            description: Proposal description.
            proposal_type: Type of proposal.
            metadata: Additional metadata.

        Returns:
            Proposal creation result.
        """
        # Check proposer stake
        if metadata and metadata.get("stake", 0) < self.min_proposal_stake:
            return {
                "status": "FAILED",
                "message": f"Insufficient stake. Need {self.min_proposal_stake}, have {metadata.get('stake', 0)}",
            }

        proposal_id = hashlib.sha256(
            f"{title}_{datetime.now(timezone.utc).timestamp()}".encode()
        ).hexdigest()[:16]

        proposal = {
            "proposal_id": proposal_id,
            "title": title,
            "description": description,
            "proposer": proposer,
            "proposal_type": proposal_type,
            "status": "active",
            "metadata": metadata or {},
            "votes_for": 0,
            "votes_against": 0,
            "votes_abstain": 0,
            "created_at": datetime.now(timezone.utc),
            "expires_at": datetime.now(timezone.utc) + timedelta(
                hours=self.voting_period_hours
            ),
        }

        self.proposals[proposal_id] = proposal

        # Log the event
        session = get_session()
        try:
            event = SystemEvent(
                event_type="governance_proposal",
                message=json.dumps({
                    "proposal_id": proposal_id,
                    "title": title,
                    "type": proposal_type,
                    "proposer": proposer,
                }),
            )
            session.add(event)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to log proposal: %s", e)
        finally:
            session.close()

        return {
            "status": "CREATED",
            "proposal_id": proposal_id,
            "title": title,
            "expires_at": proposal["expires_at"].isoformat(),
        }

    def cast_vote(
        self,
        proposal_id: str,
        voter: str,
        vote: str,
        weight: float = 1.0,
    ) -> Dict:
        """Cast a vote on a proposal.

        Args:
            proposal_id: Proposal to vote on.
            voter: Voter address.
            vote: Vote direction (for/against/abstain).
            weight: Token weight of vote.

        Returns:
            Voting result.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"status": "FAILED", "message": "Proposal not found"}

        if proposal["status"] != "active":
            return {"status": "FAILED", "message": "Proposal is not active"}

        if datetime.now(timezone.utc) > proposal["expires_at"]:
            return {"status": "FAILED", "message": "Voting period expired"}

        # Check for duplicate votes
        for v in self.votes:
            if v.proposal_id == proposal_id and v.voter == voter:
                return {"status": "FAILED", "message": "Already voted"}

        # Record the vote
        gov_vote = GovernanceVote(
            voter=voter,
            proposal_id=proposal_id,
            vote=vote,
            weight=weight,
        )
        self.votes.append(gov_vote)

        # Update proposal counts
        if vote == "for":
            proposal["votes_for"] += weight
        elif vote == "against":
            proposal["votes_against"] += weight
        elif vote == "abstain":
            proposal["votes_abstain"] += weight

        # Check if quorum is reached
        total_votes = (
            proposal["votes_for"]
            + proposal["votes_against"]
            + proposal["votes_abstain"]
        )
        quorum = self.total_supply * self.quorum_pct
        proposal["quorum_reached"] = total_votes >= quorum

        # Check if proposal passes
        if proposal["quorum_reached"] and total_votes > 0:
            for_pct = proposal["votes_for"] / total_votes
            proposal["passed"] = for_pct >= self.threshold_pct

        # Log the vote
        session = get_session()
        try:
            event = SystemEvent(
                event_type="governance_vote",
                message=json.dumps({
                    "proposal_id": proposal_id,
                    "voter": voter,
                    "vote": vote,
                    "weight": weight,
                }),
            )
            session.add(event)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to log vote: %s", e)
        finally:
            session.close()

        result = {
            "status": "VOTED",
            "proposal_id": proposal_id,
            "voter": voter,
            "vote": vote,
            "weight": weight,
            "quorum_reached": proposal["quorum_reached"],
            "passed": proposal.get("passed", False),
        }

        return result

    def execute_proposal(self, proposal_id: str) -> Dict:
        """Execute a passed proposal.

        Args:
            proposal_id: Proposal to execute.

        Returns:
            Execution result.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return {"status": "FAILED", "message": "Proposal not found"}

        if not proposal.get("passed", False):
            return {"status": "FAILED", "message": "Proposal did not pass"}

        if proposal["status"] != "active":
            return {"status": "FAILED", "message": "Proposal already executed"}

        # Execute based on proposal type
        if proposal["proposal_type"] == "fund_allocation":
            return self._execute_fund_allocation(proposal)
        elif proposal["proposal_type"] == "target_selection":
            return self._execute_target_selection(proposal)
        elif proposal["proposal_type"] == "code_upgrade":
            return self._execute_code_upgrade(proposal)
        else:
            return {"status": "FAILED", "message": f"Unknown proposal type: {proposal['proposal_type']}"}

    def _execute_fund_allocation(self, proposal: Dict) -> Dict:
        """Execute a fund allocation proposal.

        Args:
            proposal: The proposal to execute.

        Returns:
            Execution result.
        """
        target = proposal["metadata"].get("target", "unknown")
        amount = proposal["metadata"].get("amount", 0)
        purpose = proposal["metadata"].get("purpose", "")

        # Update Hydra pools
        try:
            from ..hydra import Hydra
            hydra = Hydra.load()
            hydra.update_balance("dao_treasury", -amount)
            hydra.update_balance("foundation", amount)
        except Exception as e:
            logger.error("Fund allocation execution failed: %s", e)

        proposal["status"] = "executed"
        proposal["executed_at"] = datetime.now(timezone.utc).isoformat()

        return {
            "status": "EXECUTED",
            "proposal_id": proposal["proposal_id"],
            "type": "fund_allocation",
            "target": target,
            "amount": amount,
            "purpose": purpose,
        }

    def _execute_target_selection(self, proposal: Dict) -> Dict:
        """Execute a target selection proposal.

        Args:
            proposal: The proposal to execute.

        Returns:
            Execution result.
        """
        target = proposal["metadata"].get("target", "")
        action = proposal["metadata"].get("action", "")

        proposal["status"] = "executed"
        proposal["executed_at"] = datetime.now(timezone.utc).isoformat()

        return {
            "status": "EXECUTED",
            "proposal_id": proposal["proposal_id"],
            "type": "target_selection",
            "target": target,
            "action": action,
        }

    def _execute_code_upgrade(self, proposal: Dict) -> Dict:
        """Execute a code upgrade proposal.

        Args:
            proposal: The proposal to execute.

        Returns:
            Execution result.
        """
        repo = proposal["metadata"].get("repo", "")
        branch = proposal["metadata"].get("branch", "")

        proposal["status"] = "executed"
        proposal["executed_at"] = datetime.now(timezone.utc).isoformat()

        return {
            "status": "EXECUTED",
            "proposal_id": proposal["proposal_id"],
            "type": "code_upgrade",
            "repo": repo,
            "branch": branch,
            "message": "Code upgrade queued for deployment",
        }

    def get_active_proposals(self) -> List[Dict]:
        """Get all active proposals.

        Returns:
            List of active proposals.
        """
        return [
            {k: v for k, v in p.items() if k != "metadata"}
            for p in self.proposals.values()
            if p["status"] == "active"
        ]

    def get_proposal_details(self, proposal_id: str) -> Optional[Dict]:
        """Get details of a specific proposal.

        Args:
            proposal_id: Proposal ID.

        Returns:
            Proposal details or None.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            return None

        result = {k: v for k, v in proposal.items()}
        result["votes"] = [
            {
                "voter": v.voter,
                "vote": v.vote,
                "weight": v.weight,
            }
            for v in self.votes
            if v.proposal_id == proposal_id
        ]
        return result

    def get_governance_stats(self) -> Dict:
        """Get governance statistics.

        Returns:
            Stats dict.
        """
        total_proposals = len(self.proposals)
        active_proposals = len(
            [p for p in self.proposals.values() if p["status"] == "active"]
        )
        executed_proposals = len(
            [p for p in self.proposals.values() if p["status"] == "executed"]
        )
        total_votes = len(self.votes)

        return {
            "total_proposals": total_proposals,
            "active_proposals": active_proposals,
            "executed_proposals": executed_proposals,
            "total_votes": total_votes,
            "quorum_pct": self.quorum_pct,
            "voting_period_hours": self.voting_period_hours,
            "threshold_pct": self.threshold_pct,
            "min_proposal_stake": self.min_proposal_stake,
        }
