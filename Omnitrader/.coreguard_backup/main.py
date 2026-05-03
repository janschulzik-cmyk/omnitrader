"""Omnitrader — Autonomous Multi-Axis Wealth Engine.

FastAPI entry point that wires together:
- Hydra (capital pool management)
- Striker, Foundation, Sleuth, Sentinel, Swarm modules
- Celery beat scheduler for periodic tasks
- REST API (FastAPI) and Telegram bot
- Graceful lifecycle management
"""

import os
import sys
import signal
import asyncio
import logging
import time
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Load .env file
env_path = ROOT_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# ── Logging ────────────────────────────────────────────────────────
from src.utils.logging_config import get_logger

logger = get_logger("main")

# ── AppConfig ──────────────────────────────────────────────────────

class AppConfig:
    """Central configuration loaded from settings.yaml + env."""

    def __init__(self):
        self.total_capital = float(os.environ.get("TOTAL_INITIAL_CAPITAL", "100"))
        self.testnet = os.environ.get("TESTNET", "true").lower() == "true"
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.database_url = os.environ.get(
            "DATABASE_URL",
            f"sqlite:///{ROOT_DIR}/data/omnitrader.db",
        )
        self.api_key = os.environ.get("API_KEY_SECRET", "")
        self.telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "0")
        self.llm_model = os.environ.get("LLM_MODEL", "qwen/qwen3-35b-a3b")
        self.llm_api_key = os.environ.get("LLM_API_KEY", "")
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.host = os.environ.get("API_HOST", "0.0.0.0")
        self.port = int(os.environ.get("API_PORT", "8000"))
        self.cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
        self.swarm_enabled = os.environ.get("SWARM_ENABLED", "true").lower() == "true"
        self.sleuth_enabled = os.environ.get("SLEUTH_ENABLED", "true").lower() == "true"
        self.striker_enabled = os.environ.get("STRIKER_ENABLED", "true").lower() == "true"
        self.foundation_enabled = os.environ.get("FOUNDATION_ENABLED", "true").lower() == "true"
        self.sentinel_enabled = os.environ.get("SENTINEL_ENABLED", "true").lower() == "true"
        self.offline_mode = os.environ.get("OFFLINE_MODE", "false").lower() == "true"
        self.backtest_mode = os.environ.get("BACKTEST_MODE", "false").lower() == "true"


# ── App State ──────────────────────────────────────────────────────

class AppContext:
    """Mutable application state shared across the lifecycle."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.start_time = time.time()
        self.hydra = None
        self.telegram_bot = None
        self.scheduler_task = None
        self.is_running = False

    def uptime_seconds(self) -> float:
        return time.time() - self.start_time

    def uptime_hours(self) -> float:
        return self.uptime_seconds() / 3600


# ── Lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    config = AppConfig()
    ctx = AppContext(config)
    app.state.ctx = ctx

    # 1. Initialize database
    logger.info("Initializing database at %s", config.database_url)
    from src.utils.db import get_session, init_db
    init_db()
    ctx.hydra = _init_hydra(config)

    # 2. Start Celery worker (background process)
    logger.info("Starting Celery worker...")
    celery_ok = _start_celery(config)
    if not celery_ok:
        logger.warning("Celery worker failed to start — periodic tasks will not run automatically.")

    # 3. Start Telegram bot (if token configured and not offline)
    if config.telegram_token and not config.offline_mode:
        logger.info("Starting Telegram bot...")
        from src.apis.telegram_bot import TelegramBot
        ctx.telegram_bot = TelegramBot({"config": config.__dict__})
        tg_task = asyncio.create_task(_run_telegram_async(ctx.telegram_bot))
        ctx.scheduler_task = tg_task  # keep ref to prevent GC
        logger.info("Telegram bot started.")
    elif config.telegram_token and config.offline_mode:
        logger.info("Telegram bot skipped (OFFLINE_MODE=true).")

    # 4. Start sentinel modules (if enabled and not offline)
    if config.sentinel_enabled and not config.offline_mode:
        logger.info("Starting Sentinel modules...")
        from src.sentinel.honeypot import Honeypot
        from src.sentinel.phish_detector import PhishDetector
        from src.sentinel.credential_monitor import CredentialMonitor

        try:
            honeypot = Honeypot()
            app.mount("/honeypot", honeypot.app)
            logger.info("Honeypot mounted on /honeypot")
        except Exception as e:
            logger.warning("Honeypot failed: %s", e)

        try:
            phish = PhishDetector()
            asyncio.create_task(phish.start_scanning())
            logger.info("Phish detector started.")
        except Exception as e:
            logger.warning("Phish detector failed: %s", e)

        try:
            cred = CredentialMonitor()
            asyncio.create_task(cred.start_monitoring())
            logger.info("Credential monitor started.")
        except Exception as e:
            logger.warning("Credential monitor failed: %s", e)

    # 5. Start swarm module (if enabled and not offline)
    if config.swarm_enabled and not config.offline_mode:
        logger.info("Starting swarm module...")
        try:
            from src.swarm.mesh_network import MeshNetwork
            mesh = MeshNetwork()
            asyncio.create_task(mesh.start())
            logger.info("Swarm mesh network started.")
        except Exception as e:
            logger.warning("Swarm failed: %s", e)

    # 5b. Start mesh bridge (ai-mesh operator bridge) — only if enabled and not offline
    if os.environ.get("MESH_BRIDGE_ENABLED", "false").lower() == "true" and not config.offline_mode:
        logger.info("Starting mesh bridge...")
        from src.swarm.mesh_bridge import get_mesh_bridge, patch_trade_executor_post_trade
        bridge = get_mesh_bridge()
        ctx.mesh_bridge = bridge
        bridge_start = asyncio.create_task(bridge.start())
        ctx.bridge_start_task = bridge_start
        patch_trade_executor_post_trade()
        logger.info("Mesh bridge started.")

    logger.info(
        "Omnitrader started. Total capital: $%.2f | API on port %d",
        config.total_capital, config.port,
    )
    yield  # ← server is running here

    # ── Shutdown ────────────────────────────────────────────────
    logger.info("Shutting down Omnitrader...")

    # Stop Celery worker
    _stop_celery()

    # Stop Telegram bot
    if ctx.telegram_bot:
        ctx.telegram_bot.stop()

    # Stop swarm
    try:
        from src.swarm.mesh_network import MeshNetwork
        # MeshNetwork instances don't have a global ref, but we signal graceful exit
        logger.info("Swarm shutdown requested.")
    except Exception:
        pass

    logger.info("Omnitrader stopped gracefully.")


def _init_hydra(config: AppConfig):
    """Initialize Hydra capital pool manager."""
    from src.hydra import Hydra
    h = Hydra.load(config=config)
    h.initialize_pools(config.total_capital)
    return h


def _start_celery(config: AppConfig):
    """Start Celery worker as a subprocess.

    Returns True if the worker process was launched successfully.
    """
    try:
        from celery_app import celery_app

        import subprocess
        worker_proc = subprocess.Popen(
            [
                sys.executable,
                "-m", "celery",
                "-A", "celery_app:celery_app",
                "worker",
                "--loglevel=" + config.log_level,
                "--concurrency=4",
                "--pool=solo",  # solo for single-process (no fork on Linux)
            ],
            cwd=str(ROOT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Give it a moment to start
        time.sleep(2)
        if worker_proc.poll() is None:
            logger.info("Celery worker launched (PID %d).", worker_proc.pid)
            return True
        else:
            logger.error("Celery worker exited immediately.")
            return False
    except Exception as e:
        logger.error("Failed to start Celery: %s", e)
        return False


def _stop_celery():
    """Stop the Celery worker subprocess."""
    try:
        from celery_app import celery_app
        celery_app.control.shutdown(timeout=10)
        logger.info("Celery worker shutdown requested.")
    except Exception as e:
        logger.warning("Could not stop Celery gracefully: %s", e)


async def _run_telegram_async(bot):
    """Run the Telegram bot in an asyncio-compatible way."""
    try:
        await bot.start()
    except Exception as e:
        logger.error("Telegram bot error: %s", e)


# ── FastAPI App ────────────────────────────────────────────────────

app = FastAPI(
    title="Omnitrader",
    description="Autonomous Multi-Axis Wealth Engine",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health endpoint (no auth)
@app.get("/health")
async def health_check():
    """Health check — checks Redis connectivity if configured."""
    ctx = app.state.ctx
    redis_ok = False
    try:
        import redis
        r = redis.from_url(ctx.config.redis_url, socket_timeout=2)
        r.ping()
        redis_ok = True
        r.close()
    except Exception:
        pass

    db_ok = False
    try:
        from src.utils.db import get_session
        from sqlalchemy import text
        session = get_session()
        session.execute(text("SELECT 1"))
        session.close()
        db_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if (redis_ok and db_ok) else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_hours": round(ctx.uptime_hours(), 2) if ctx else 0,
        "redis": redis_ok,
        "database": db_ok,
        "pools": {
            k: round(v, 2)
            for k, v in ctx.hydra.get_all_balances().items()
        } if ctx and ctx.hydra else {},
    }


# ── API Router ─────────────────────────────────────────────────────
from src.apis.routes import router as api_router
app.include_router(api_router)


# ── Direct Execution ───────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=AppConfig().host,
        port=AppConfig().port,
        reload=False,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
