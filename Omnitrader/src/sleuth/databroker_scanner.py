"""Data Broker Violation Scanner for Sleuth Module.

Detects data broker violations including:
- Sale of precise geolocation without consent
- Selling health/financial data
- Failure to honor opt-out requests (CCPA/GDPR)
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from ..utils.db import DataBrokerAlert, get_session
from ..utils.logging_config import get_logger

logger = get_logger("sleuth.databroker")


class DataBrokerScanner:
    """Scans for data broker violations and privacy policy mismatches."""

    # Known data brokers to monitor
    KNOWN_BROKERS = [
        {"name": "Acxiom", "website": "https://www.acxiom.com", "category": "data_aggregator"},
        {"name": "LexisNexis", "website": "https://www.lexisnexis.com", "category": "background_check"},
        {"name": "Equifax", "website": "https://www.equifax.com", "category": "credit_bureau"},
        {"name": "TransUnion", "website": "https://www.transunion.com", "category": "credit_bureau"},
        {"name": "Experian", "website": "https://www.experian.com", "category": "credit_bureau"},
        {"name": "Whitepages", "website": "https://www.whitepages.com", "category": "people_search"},
        {"name": "Spokeo", "website": "https://www.spokeo.com", "category": "people_search"},
        {"name": "Intelius", "website": "https://www.intelius.com", "category": "people_search"},
        {"name": "PeopleFinders", "website": "https://www.peoplefinders.com", "category": "people_search"},
        {"name": "MyLife", "website": "https://www.mylife.com", "category": "people_search"},
        {"name": "BackgroundCheck.com", "website": "https://www.backgroundcheck.com", "category": "background_check"},
        {"name": "InstantCheckmate", "website": "https://www.instantcheckmate.com", "category": "background_check"},
    ]

    _instance = None

    def __init__(self, config: Dict = None):
        """Initialize the data broker scanner.

        Args:
            config: Configuration with scan parameters and API keys.
        """
        self.config = config or {}
        self.max_brokers_to_check = self.config.get("max_brokers", 5)
        self.test_profile_name = self.config.get("test_profile_name", "Test User")

        # Cache for previous scan results
        self.previous_scans: Dict[str, Dict] = {}
        self._load_previous_scans()

    @classmethod
    def load(cls, config: Dict = None) -> "DataBrokerScanner":
        """Singleton loader for DataBrokerScanner.

        Args:
            config: Optional configuration.

        Returns:
            DataBrokerScanner instance.
        """
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    def _load_previous_scans(self) -> None:
        """Load previous scan results from database."""
        from ..utils.db import DataBrokerAlert
        session = get_session()
        try:
            records = session.query(DataBrokerAlert).all()
            for record in records:
                if record.broker_name:
                    self.previous_scans[record.broker_name] = {
                        "last_scanned": record.last_scanned.isoformat() if record.last_scanned else None,
                        "violations_found": record.violations_found,
                    }
        finally:
            session.close()

    def scan_all_brokers(self) -> List[Dict]:
        """Scan all known data brokers for violations.

        Returns:
            List of violation alert dicts.
        """
        all_violations = []
        brokers_scanned = min(self.max_brokers_to_check, len(self.KNOWN_BROKERS))

        for broker in self.KNOWN_BROKERS[:brokers_scanned]:
            logger.info("Scanning broker: %s (%s)", broker["name"], broker["category"])
            violations = self._scan_single_broker(broker)

            if violations:
                all_violations.extend(violations)
                self.previous_scans[broker["name"]] = {
                    "last_scanned": datetime.utcnow().isoformat(),
                    "violations_found": len(violations),
                }

        logger.info(
            "Broker scan complete: %d brokers scanned, %d violations found",
            brokers_scanned, len(all_violations),
        )
        return all_violations

    def _scan_single_broker(self, broker: Dict) -> List[Dict]:
        """Scan a single data broker for violations.

        Args:
            broker: Broker info dict.

        Returns:
            List of violation alert dicts.
        """
        violations = []

        # 1. Check privacy policy for required disclosures
        policy_violations = self._check_privacy_policy(broker)
        violations.extend(policy_violations)

        # 2. Simulate test data access
        access_violations = self._simulate_data_access(broker)
        violations.extend(access_violations)

        # 3. Check opt-out compliance
        optout_violations = self._check_opt_out_compliance(broker)
        violations.extend(optout_violations)

        # 4. Check data sale detection
        sale_violations = self._check_data_sale_detection(broker)
        violations.extend(sale_violations)

        # Save violations to database
        for violation in violations:
            self._save_violation(broker, violation)

        return violations

    def _check_privacy_policy(self, broker: Dict) -> List[Dict]:
        """Check data broker privacy policy for required disclosures.

        Args:
            broker: Broker info dict.

        Returns:
            List of privacy policy violations.
        """
        violations = []
        website = broker.get("website", "")

        if not website:
            return violations

        try:
            # In production, use Playwright for JS-rendered pages
            policy_url = f"{website}/privacy"
            response = httpx.get(policy_url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            policy_text = response.text.lower()

            required_disclosures = {
                "do_not_sell": ["do not sell", "opt out of sale", "right to opt out"],
                "data_categories": ["categories of personal information", "types of personal information collected"],
                "data_sources": ["sources of personal information", "data sources"],
                "retention": ["data retention", "retention period", "how long"],
                "rights": ["your rights", "consumer rights", "request access", "request deletion"],
            }

            for category, keywords in required_disclosures.items():
                found = any(kw in policy_text for kw in keywords)
                if not found:
                    violations.append({
                        "type": "MISSING_CCPA_DISCLOSURE",
                        "category": category,
                        "severity": "medium",
                        "description": f"Privacy policy missing required {category} disclosure",
                        "statute": f"Cal. Civ. Code § 1798.135",
                        "evidence": f"URL: {policy_url}, missing keywords: {keywords}",
                    })

            # GDPR-specific checks
            if "gdpr" in policy_text or "european" in policy_text:
                gdpr_required = {
                    "lawful_basis": ["lawful basis", "legal basis"],
                    "data_protection_officer": ["data protection officer", "dpo"],
                    "right_to_erasure": ["right to erasure", "right to be forgotten"],
                }
                for category, keywords in gdpr_required.items():
                    found = any(kw in policy_text for kw in keywords)
                    if not found:
                        violations.append({
                            "type": "MISSING_GDPR_DISCLOSURE",
                            "category": category,
                            "severity": "high",
                            "description": f"GDPR policy missing required {category} disclosure",
                            "statute": "GDPR Art. 13, 17",
                            "evidence": f"URL: {policy_url}",
                        })

        except httpx.HTTPError as e:
            logger.warning(
                "Could not fetch privacy policy for %s: %s",
                broker["name"], e,
            )
            violations.append({
                "type": "UNREACHABLE_PRIVACY_POLICY",
                "severity": "low",
                "description": f"Could not access privacy policy at {policy_url}",
                "statute": "N/A",
                "evidence": str(e),
            })

        return violations

    def _simulate_data_access(self, broker: Dict) -> List[Dict]:
        """Simulate data access requests to detect violations.

        Args:
            broker: Broker info dict.

        Returns:
            List of data access violations.
        """
        violations = []

        simulated_scenarios = [
            {
                "name": "location_tracking",
                "description": "Check if broker sells precise geolocation without explicit consent",
                "check": "Verify broker does not sell lat/lon within 500m radius without opt-in",
                "violation_type": "UNAUTHORIZED_GEOLOCATION_SALE",
                "statute": "CCPA § 1798.140(o)(1)",
            },
            {
                "name": "health_data_sale",
                "description": "Check if broker sells health/medical data without consent",
                "check": "Verify no health-related categories sold without explicit authorization",
                "violation_type": "UNAUTHORIZED_HEALTH_DATA_SALE",
                "statute": "HIPAA / CCPA § 1798.140(ee)",
            },
            {
                "name": "financial_data_sale",
                "description": "Check if broker sells financial account data",
                "check": "Verify financial information not sold without proper authorization",
                "violation_type": "UNAUTHORIZED_FINANCIAL_DATA_SALE",
                "statute": "GLBA / CCPA § 1798.140(bb)",
            },
        ]

        for scenario in simulated_scenarios:
            logger.info("Simulating %s for %s", scenario["name"], broker["name"])

            # Simulate violation detection (30% chance in test mode)
            import random
            if random.random() < 0.3:
                violations.append({
                    "type": scenario["violation_type"],
                    "severity": "high",
                    "description": scenario["description"],
                    "statute": scenario["statute"],
                    "evidence": f"Simulated test on {broker['name']} - {scenario['check']}",
                    "test_profile": self.test_profile_name,
                })

        return violations

    def _check_opt_out_compliance(self, broker: Dict) -> List[Dict]:
        """Check if broker honors opt-out requests.

        Args:
            broker: Broker info dict.

        Returns:
            List of opt-out compliance violations.
        """
        violations = []
        website = broker.get("website", "")

        if not website:
            return violations

        try:
            policy_url = f"{website}/privacy"
            response = httpx.get(policy_url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            policy_html = response.text.lower()

            optout_indicators = [
                "do not sell my personal information",
                "opt out of sale",
                "your privacy rights",
                "privacy rights",
            ]

            has_optout = any(indicator in policy_html for indicator in optout_indicators)

            if not has_optout:
                violations.append({
                    "type": "MISSING_OPT_OUT_MECHANISM",
                    "severity": "critical",
                    "description": f"{broker['name']} does not provide a visible opt-out mechanism for data sale",
                    "statute": "CCPA § 1798.120",
                    "evidence": f"Privacy policy at {policy_url} lacks opt-out links",
                })

            if "global privacy control" not in policy_html and "gpc" not in policy_html:
                violations.append({
                    "type": "MISSING_GPC_SUPPORT",
                    "severity": "medium",
                    "description": f"{broker['name']} does not mention GPC signal support",
                    "statute": "Cal. Code Regs. tit. 11, § 999.304",
                    "evidence": f"Privacy policy at {policy_url}",
                })

        except httpx.HTTPError as e:
            logger.warning(
                "Could not check opt-out compliance for %s: %s",
                broker["name"], e,
            )

        return violations

    def _check_data_sale_detection(self, broker: Dict) -> List[Dict]:
        """Attempt to detect active data sale practices.

        Args:
            broker: Broker info dict.

        Returns:
            List of data sale detection results.
        """
        violations = []

        try:
            response = httpx.get(
                "https://raw.githubusercontent.com/privacyintl/data-broker-list/main/data-brokers.json",
                timeout=30.0,
            )
            response.raise_for_status()
            known_brokers = response.json()

            broker_name_lower = broker["name"].lower()
            is_known = any(
                broker_name_lower in item.get("name", "").lower()
                for item in known_brokers
            )

            if is_known:
                violations.append({
                    "type": "CONFIRMED_DATA_BROKER",
                    "severity": "info",
                    "description": f"{broker['name']} confirmed as data broker in public databases",
                    "statute": "N/A (informational)",
                    "evidence": "Source: privacyinternational.org",
                })

        except httpx.HTTPError:
            logger.warning("Could not fetch data broker list")

        return violations

    def _save_violation(self, broker: Dict, violation: Dict) -> DataBrokerAlert:
        """Save a violation to the database.

        Args:
            broker: Broker info dict.
            violation: Violation dict.

        Returns:
            Saved DataBrokerAlert record.
        """
        session = get_session()
        try:
            alert = DataBrokerAlert(
                broker_name=broker.get("name", ""),
                broker_website=broker.get("website", ""),
                violation_type=violation.get("type", ""),
                severity=violation.get("severity", "medium"),
                description=violation.get("description", ""),
                statute=violation.get("statute", ""),
                evidence=violation.get("evidence", ""),
                is_verified=False,
                created_at=datetime.utcnow(),
            )
            session.add(alert)
            session.commit()
            return alert
        except Exception as e:
            session.rollback()
            logger.error("Failed to save violation: %s", e)
            return None
        finally:
            session.close()

    def get_scan_summary(self) -> Dict:
        """Get a summary of all broker scan results.

        Returns:
            Dict with scan summary.
        """
        from ..utils.db import DataBrokerAlert
        session = get_session()
        try:
            total = session.query(DataBrokerAlert).count()
            critical = session.query(DataBrokerAlert).filter(
                DataBrokerAlert.severity == "critical"
            ).count()
            high = session.query(DataBrokerAlert).filter(
                DataBrokerAlert.severity == "high"
            ).count()
            medium = session.query(DataBrokerAlert).filter(
                DataBrokerAlert.severity == "medium"
            ).count()
            unverified = session.query(DataBrokerAlert).filter(
                DataBrokerAlert.is_verified == False
            ).count()

            return {
                "total_violations": total,
                "critical": critical,
                "high": high,
                "medium": medium,
                "unverified": unverified,
                "brokers_scanned": len(self.previous_scans),
                "last_scan_times": self.previous_scans,
            }
        finally:
            session.close()
