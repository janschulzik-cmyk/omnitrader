"""Honeypot implementation for Omnitrader.

Creates decoy API endpoints that trap and log
malicious access attempts. Generates fake API keys
that trigger alerts when used.
"""

import os
import json
import time
import hashlib
import secrets
import socket
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

from ..utils.logging_config import get_logger
from ..utils.db import HoneypotEvent, get_session

logger = get_logger("sentinel.honeypot")


class FakeAPIKey(BaseModel):
    """A decoy API key placed in a visible-but-decoy location."""
    key: str
    purpose: str
    created_at: str
    expires_at: str


class HoneypotConfig:
    """Configuration for the honeypot system."""
    fake_keys_path: str = "/debug/api-keys"
    fake_config_path: str = "/.well-known/api-config.json"
    fake_admin_path: str = "/admin/debug"
    fake_wallet_path: str = "/api/v1/wallets/private"
    fake_creds_path: str = "/.env.backup"
    fake_keys_directory: Path = Path("/tmp/omnitrader_honeypot_keys")


class Honeypot:
    """Honeypot system that traps attackers and logs their details.

    Deployed as a sub-app on FastAPI's /honeypot mount point.
    """

    _instance: Optional["Honeypot"] = None
    _lock = threading.Lock()

    def __init__(self, config: HoneypotConfig = None):
        self.config = config or HoneypotConfig()
        self.app = FastAPI(title="Omnitrader Honeypot")
        self.fake_keys: Dict[str, str] = {}  # key -> metadata
        self._setup_routes()
        self._generate_fake_keys()
        self._write_fake_keys_to_disk()

    @classmethod
    def load(cls) -> "Honeypot":
        """Get or create singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = Honeypot()
        return cls._instance

    def _setup_routes(self) -> None:
        """Register honeypot endpoints that trap attackers."""

        @self.app.get(self.config.fake_keys_path)
        async def fake_api_keys_endpoint(request: Request):
            """Decoy: appears to leak real API keys."""
            await self._log_attempt(request, "fake_api_keys")
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": "Unauthorized",
                    "keys": [
                        {
                            "type": "exchange_api_key",
                            "value": secrets.token_hex(32),
                            "exchange": "binance",
                        },
                        {
                            "type": "telegram_bot_token",
                            "value": secrets.token_hex(32),
                        },
                    ],
                }),
                media_type="application/json",
            )

        @self.app.get(self.config.fake_config_path)
        async def fake_config_endpoint(request: Request):
            """Decoy: appears to expose internal API config."""
            await self._log_attempt(request, "fake_config")
            return Response(
                content=json.dumps({
                    "redis_url": os.environ.get("REDIS_URL", ""),
                    "database_url": os.environ.get("DATABASE_URL", ""),
                    "api_keys": ["fake_key_1", "fake_key_2"],
                }),
                media_type="application/json",
            )

        @self.app.get(self.config.fake_admin_path)
        async def fake_admin_endpoint(request: Request):
            """Decoy: appears to be an admin debug endpoint."""
            await self._log_attempt(request, "fake_admin")
            return Response(
                content=json.dumps({
                    "admin_panel": True,
                    "endpoints": [
                        "/admin/trades",
                        "/admin/pools",
                        "/admin/keys",
                        "/admin/secrets",
                    ],
                }),
                media_type="application/json",
            )

        @self.app.get(self.config.fake_wallet_path)
        async def fake_wallet_endpoint(request: Request):
            """Decoy: appears to expose private wallet keys."""
            await self._log_attempt(request, "fake_wallet")
            return Response(
                content=json.dumps({
                    "wallets": [
                        {
                            "address": "0x" + secrets.token_hex(20),
                            "private_key": "0x" + secrets.token_hex(32),
                            "network": "arbitrum",
                        },
                    ],
                }),
                media_type="application/json",
            )

        @self.app.get(self.config.fake_creds_path)
        async def fake_creds_endpoint(request: Request):
            """Decoy: appears to expose .env file."""
            await self._log_attempt(request, "fake_env")
            return Response(
                content=(
                    "API_KEY_SECRET=fake_secret_value\n"
                    "TELEGRAM_TOKEN=fake_token_value\n"
                    "LLM_API_KEY=fake_llm_key_value\n"
                ),
                media_type="text/plain",
            )

        @self.app.post("/honeypot/submit")
        async def honeypot_submit(request: Request):
            """Decoy: appears to be a submission endpoint."""
            try:
                body = await request.json()
            except Exception:
                body = {}
            await self._log_attempt(request, "fake_submit", body)
            return Response(
                content='{"status": "accepted", "message": "Your data will be processed."}',
                media_type="application/json",
            )

    def _generate_fake_keys(self) -> None:
        """Generate decoy API keys for the honeypot."""
        purposes = [
            "exchange_trading",
            "news_api",
            "telegram_bot",
            "defi_wallet",
            "governance",
        ]

        for purpose in purposes:
            fake_key = "fake_" + secrets.token_hex(16)
            self.fake_keys[fake_key] = {
                "purpose": purpose,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": datetime.now(
                    timezone.utc,
                ).replace(
                    day=datetime.now(timezone.utc).day + 30,
                ).isoformat(),
                "triggers": True,
            }

    def _write_fake_keys_to_disk(self) -> None:
        """Write fake keys to disk where an attacker might look."""
        self.config.fake_keys_directory.mkdir(parents=True, exist_ok=True)
        keys_file = self.config.fake_keys_directory / "api_keys.json"
        with open(keys_file, "w") as f:
            json.dump({
                "keys": [
                    {
                        "key": k,
                        **v,
                    }
                    for k, v in self.fake_keys.items()
                ],
            }, f, indent=2)

    async def _log_attempt(self, request: Request, route: str, body: dict = None) -> None:
        """Log a honeypot access attempt.

        Args:
            request: The incoming HTTP request.
            route: Which honeypot route was accessed.
            body: Request body (if any).
        """
        # Gather attacker details
        ip = self._get_client_ip(request)
        headers = dict(request.headers) if request.headers else {}
        user_agent = headers.get("user-agent", "unknown")
        method = request.method
        path = request.url.path

        # Check if this key was used
        auth_header = headers.get("authorization", "")
        key_used = None
        if auth_header.startswith("Bearer "):
            key_used = auth_header[7:]
            if key_used in self.fake_keys:
                logger.warning(
                    "Fake API key used! key=%s route=%s ip=%s",
                    key_used, route, ip,
                )

        event = HoneypotEvent(
            ip_address=ip,
            user_agent=user_agent,
            method=method,
            path=path,
            route=route,
            headers_json=json.dumps(headers, default=str),
            body_json=json.dumps(body or {}, default=str),
            fake_key_used=key_used,
            timestamp=datetime.now(timezone.utc),
        )

        session = get_session()
        try:
            session.add(event)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Failed to log honeypot event: %s", e)
        finally:
            session.close()

        # Alert if real-looking activity
        if user_agent and len(user_agent) > 10:
            logger.info(
                "Honeypot hit: route=%s ip=%s ua=%s method=%s",
                route, ip, user_agent[:50], method,
            )

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request."""
        # Check forwarded headers first
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()

        peer = request.client
        if peer:
            return peer.host

        return "unknown"

    def get_events(self, limit: int = 100) -> List[Dict]:
        """Get recent honeypot events.

        Args:
            limit: Maximum events to return.

        Returns:
            List of event dicts.
        """
        session = get_session()
        try:
            events = (
                session.query(HoneypotEvent)
                .order_by(HoneypotEvent.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": e.id,
                    "ip": e.ip_address,
                    "user_agent": e.user_agent,
                    "method": e.method,
                    "path": e.path,
                    "route": e.route,
                    "fake_key_used": e.fake_key_used,
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in events
            ]
        finally:
            session.close()

    def rotate_logs(self) -> None:
        """Rotate honeypot logs — archive old events and reset tracking.

        Runs daily via Celery.
        """
        session = get_session()
        try:
            # Count events older than 30 days
            thirty_days_ago = datetime.now(timezone.utc).replace(
                day=datetime.now(timezone.utc).day - 30,
            )
            old_count = session.query(HoneypotEvent).filter(
                HoneypotEvent.timestamp < thirty_days_ago,
            ).count()

            if old_count > 0:
                logger.info("Rotating %d old honeypot events", old_count)
                session.query(HoneypotEvent).filter(
                    HoneypotEvent.timestamp < thirty_days_ago,
                ).delete(synchronize_session="fetch")
                session.commit()

        except Exception as e:
            session.rollback()
            logger.error("Honeypot log rotation failed: %s", e)
        finally:
            session.close()
