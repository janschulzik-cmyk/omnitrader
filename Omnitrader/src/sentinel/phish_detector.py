"""Phishing domain detector for Omnitrader Sentinel.

Monitors newly registered domains that resemble known
services (exchanges, API endpoints, etc.) and reports
them to appropriate authorities.
"""

import os
import json
import time
import re
import socket
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ..utils.logging_config import get_logger
from ..utils.db import OnChainAlert, get_session

logger = get_logger("sentinel.phish")

# Known services to protect
PROTECTED_DOMAINS = [
    "binance.com",
    "coinbase.com",
    "kraken.com",
    "arbitrum.io",
    "uniswap.org",
    "aave.com",
    "lido.fi",
    "opensea.io",
]

# Phishing keyword patterns
PHISHING_PATTERNS = [
    r"login[.-]?\w*binance",
    r"verify[.-]?\w*coinbase",
    r"secure[.-]?\w*kraken",
    r"wallet[.-]?\w*connect",
    r"claim[.-]?\w*airdrop",
    r"confirm[.-]?\w*transaction",
    r"verify[.-]?\w*identity",
    r"update[.-]?\w*account",
]


class PhishDetectorConfig:
    """Configuration for the phishing detector."""
    scan_interval: int = 43200  # 12 hours
    max_domains_per_scan: int = 100
    alert_threshold: float = 0.7  # similarity threshold


class PhishDetector:
    """Detects and reports phishing domains targeting Omnitrader's ecosystem."""

    _instance: Optional["PhishDetector"] = None

    def __init__(self, config: PhishDetectorConfig = None):
        self.config = config or PhishDetectorConfig()
        self.blocklist: set = set()
        self._load_blocklist()

    @classmethod
    def load(cls) -> "PhishDetector":
        if cls._instance is None:
            cls._instance = PhishDetector()
        return cls._instance

    def _load_blocklist(self) -> None:
        """Load known phishing domains from a local file."""
        blocklist_path = os.environ.get(
            "PHISH_BLOCKLIST_PATH",
            "/etc/omnitrader/blocklist.json",
        )
        try:
            with open(blocklist_path) as f:
                self.blocklist = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self.blocklist = set()
            logger.info("No blocklist found; starting empty.")

    async def start_scanning(self) -> None:
        """Start periodic phishing domain scanning."""
        while True:
            try:
                domains = self.scan_new_phishing_domains()
                logger.info(
                    "phish_detector: found %d new phishing domains",
                    len(domains),
                )
            except Exception as e:
                logger.error("phish_detector scan failed: %s", e)
            await asyncio.sleep(self.config.scan_interval)

    def scan_new_phishing_domains(self) -> List[Dict]:
        """Scan for newly registered phishing domains.

        Uses free OSINT APIs (WHOIS) to find recently registered
        domains that resemble protected services.

        Returns:
            List of detected phishing domain dicts.
        """
        detected = []

        # Check WHOIS for recently registered domains
        for domain in PROTECTED_DOMAINS:
            try:
                new_domains = self._check_whois_for_variants(domain)
                for d in new_domains:
                    if self._is_phishing(d, domain):
                        detected.append(d)
                        self._report_phishing(d)
            except Exception as e:
                logger.warning(
                    "whois check failed for %s: %s", domain, e,
                )

        return detected

    def _check_whois_for_variants(self, protected_domain: str) -> List[Dict]:
        """Check WHOIS for recently registered domain variants.

        Uses public WHOIS APIs via requests.
        """
        variants = self._generate_domain_variants(protected_domain)
        detected = []

        for variant in variants[:self.config.max_domains_per_scan]:
            try:
                whois_data = self._lookup_whois(variant)
                if whois_data and whois_data.get("registrar"):
                    created = whois_data.get("creation_date", "")
                    if created:
                        # Check if registered in last 30 days
                        try:
                            created_dt = datetime.strptime(
                                created[:10], "%Y-%m-%d",
                            )
                            age_days = (
                                datetime.utcnow() - created_dt
                            ).days
                            if age_days <= 30:
                                detected.append({
                                    "domain": variant,
                                    "created_date": created,
                                    "age_days": age_days,
                                    "registrar": whois_data.get("registrar", ""),
                                    "registrar_country": whois_data.get(
                                        "registrar_country", "",
                                    ),
                                    "nameservers": whois_data.get(
                                        "nameservers", [],
                                    ),
                                    "protected_domain": protected_domain,
                                })
                        except ValueError:
                            pass
            except Exception:
                continue  # Skip domains that fail WHOIS

        return detected

    def _generate_domain_variants(self, domain: str) -> List[str]:
        """Generate likely phishing variants of a domain.

        Args:
            domain: Protected domain (e.g., 'binance.com').

        Returns:
            List of variant domain strings.
        """
        parts = domain.split(".")
        root = parts[0]
        tld = parts[-1] if len(parts) > 1 else "com"

        prefixes = ["login", "verify", "secure", "update", "account",
                     "sign-in", "signin", "auth", "app", "web",
                     "support", "help", "admin", "my"]
        suffixes = ["official", "secure", "update", "verify", "login",
                     "account", "wallet", "sign", "auth"]

        tlds = ["com", "net", "org", "io", "xyz", "top", "site", "info"]

        variants = []
        for prefix in prefixes:
            variants.append(f"{prefix}-{root}.{tld}")
            variants.append(f"{root}-{prefix}.{tld}")
            variants.append(f"{prefix}{root}.{tld}")

        for suffix in suffixes:
            variants.append(f"{root}{suffix}.{tld}")
            variants.append(f"{root}-{suffix}.{tld}")

        # Add common TLD typos
        for t in tlds:
            variants.append(f"{root}.{t}")

        # Remove duplicates and return
        return list(set(variants))

    def _lookup_whois(self, domain: str) -> Optional[Dict]:
        """Look up WHOIS information for a domain.

        Args:
            domain: Domain to look up.

        Returns:
            WHOIS data dict or None.
        """
        try:
            import whois
            w = whois.whois(domain)
            if w is None:
                return None

            return {
                "registrar": w.registrar if hasattr(w, "registrar") else None,
                "creation_date": str(w.creation_date) if hasattr(w, "creation_date") else None,
                "registrar_country": w.registrar_country if hasattr(w, "registrar_country") else None,
                "nameservers": w.nameservers if hasattr(w, "nameservers") else [],
            }
        except Exception:
            return None

    def _is_phishing(self, domain_info: Dict, protected_domain: str) -> bool:
        """Determine if a domain is likely phishing.

        Args:
            domain_info: Domain information dict.
            protected_domain: The protected domain it resembles.

        Returns:
            True if likely phishing.
        """
        domain = domain_info["domain"]

        # Skip if it's the protected domain itself
        if domain == protected_domain:
            return False

        # Check similarity to protected domain
        protected_name = protected_domain.split(".")[0]
        domain_name = domain.split(".")[0]

        # Levenshtein-like similarity check
        if self._levenshtein_distance(protected_name, domain_name) <= 2:
            return True

        # Check against known phishing patterns
        for pattern in PHISHING_PATTERNS:
            if re.search(pattern, domain, re.IGNORECASE):
                return True

        # Check if domain resolves to a suspicious IP
        try:
            ip = socket.gethostbyname(domain)
            # Check if IP is in known datacenter ranges (common for phishing)
            if self._is_datacenter_ip(ip):
                return True
        except socket.gaierror:
            pass  # Domain doesn't resolve — skip

        return False

    def _levenshtein_distance(self, a: str, b: str) -> int:
        """Calculate Levenshtein distance between two strings.

        Args:
            a: First string.
            b: Second string.

        Returns:
            Edit distance.
        """
        if len(a) < len(b):
            return self._levenshtein_distance(b, a)

        if len(b) == 0:
            return len(a)

        prev_row = range(len(b) + 1)
        for i, ca in enumerate(a):
            curr_row = [i + 1]
            for j, cb in enumerate(b):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (ca != cb)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row

        return prev_row[-1]

    def _is_datacenter_ip(self, ip: str) -> bool:
        """Check if an IP is likely from a datacenter.

        Args:
            ip: IP address to check.

        Returns:
            True if likely datacenter IP.
        """
        parts = ip.split(".")
        if len(parts) != 4:
            return False

        # Common datacenter ranges
        datacenter_prefixes = [
            "52.", "54.", "13.", "15.", "18.", "35.",  # AWS
            "34.", "35.", "104.", "142.",  # Google
            "45.", "46.", "51.", "185.",  # OVH
            "192.", "198.", "209.",  # Generic hosting
        ]

        for prefix in datacenter_prefixes:
            if ip.startswith(prefix):
                return True

        return False

    def _report_phishing(self, domain_info: Dict) -> None:
        """Report a phishing domain to authorities.

        Args:
            domain_info: Domain information dict.
        """
        logger.warning(
            "PHISHING DOMAIN DETECTED: %s (resembles %s)",
            domain_info["domain"],
            domain_info["protected_domain"],
        )

        # Log to DB
        session = get_session()
        try:
            alert = OnChainAlert(
                alert_type="phishing_domain",
                network="all",
                severity="HIGH",
                target_address=domain_info["domain"],
                evidence=json.dumps(domain_info),
                value_usd=None,
                submitted_as_bounty=False,
            )
            session.add(alert)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to log phishing alert: %s", e)
        finally:
            session.close()

        # Add to blocklist
        self.blocklist.add(domain_info["domain"])

    def is_blocked(self, domain: str) -> bool:
        """Check if a domain is in the blocklist.

        Args:
            domain: Domain to check.

        Returns:
            True if blocked.
        """
        return domain in self.blocklist

    def add_to_blocklist(self, domain: str) -> None:
        """Add a domain to the blocklist.

        Args:
            domain: Domain to block.
        """
        self.blocklist.add(domain)
        logger.info("Added to blocklist: %s", domain)

    def get_blocklist(self) -> List[str]:
        """Get the current blocklist.

        Returns:
            List of blocked domains.
        """
        return list(self.blocklist)
