"""Telegram Bot for Omnitrader.

Provides mobile control via Telegram commands:
/status, /pause, /resume, /balance, /scan, /rebalance
"""

import os
import json
from typing import Dict, Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .auth import verify_telegram_user, get_telegram_admin_id
from ..utils.logging_config import get_logger

logger = get_logger("apis.telegram_bot")


class TelegramBot:
    """Telegram bot for mobile control of Omnitrader."""

    def __init__(self, config: Dict = None):
        """Initialize the Telegram bot.

        Args:
            config: Bot configuration dict.
        """
        self.config = config or {}
        self.token = os.environ.get("TELEGRAM_TOKEN", "")
        self.admin_id = get_telegram_admin_id()
        self.app = None
        self.running = False

        # Command descriptions
        self.commands = {
            "status": "Show full system status",
            "balance": "Show capital pool balances",
            "trades": "Show recent trades",
            "pause": "Pause the Striker module",
            "resume": "Resume the Striker module",
            "scan": "Trigger a full on-chain scan",
            "rebalance": "Trigger Foundation rebalance",
            "analyze": "Run learning analysis",
            "help": "Show this help message",
        }

    def _check_admin(self, update: Update) -> bool:
        """Check if the user is an authorized admin.

        Args:
            update: Telegram update.

        Returns:
            True if admin.
        """
        user_id = update.effective_user.id if update.effective_user else 0
        if not verify_telegram_user(user_id):
            self._send_message(
                update.effective_chat.id,
                "⛔ Unauthorized. Only the admin can use this bot.",
            )
            return False
        return True

    def _send_message(self, chat_id: int, text: str) -> None:
        """Send a message to a chat.

        Args:
            chat_id: Telegram chat ID.
            text: Message text.
        """
        if self.app:
            try:
                self.app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error("Failed to send Telegram message: %s", e)

    async def cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /status command."""
        if not self._check_admin(update):
            return

        from ..hydra import Hydra
        from ..sleuth.bounty_reporter import BountyReporter
        from ..sleuth.databroker_scanner import DataBrokerScanner

        hydra = Hydra.load()
        pools = hydra.get_all_balances()

        reporter = BountyReporter.load()
        submissions = reporter.get_submission_history()

        broker_scanner = DataBrokerScanner.load()
        broker_summary = broker_scanner.get_scan_summary()

        text = f"""
📊 *Omnitrader Status*

💰 *Pools:*
  Moat: ${pools.get('moat', 0):,.2f}
  Striker: ${pools.get('striker', 0):,.2f}
  Foundation: ${pools.get('foundation', 0):,.2f}
  Total: ${sum(pools.values()):,.2f}

📬 *Recent Submissions:*
{self._format_submissions(submissions)}

🔍 *Broker Scans:*
  Violations: {broker_summary.get('total_violations', 0)}
  Critical: {broker_summary.get('critical', 0)}
  Unverified: {broker_summary.get('unverified', 0)}
"""
        self._send_message(update.effective_chat.id, text)

    async def cmd_balance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /balance command."""
        if not self._check_admin(update):
            return

        from ..hydra import Hydra

        hydra = Hydra.load()
        pools = hydra.get_all_balances()

        text = f"""
💰 *Pool Balances*

  Moat (safe cash): ${pools.get('moat', 0):,.2f}
  Striker (trading): ${pools.get('striker', 0):,.2f}
  Foundation (growth): ${pools.get('foundation', 0):,.2f}
  ───────────────────────
  Total capital: ${sum(pools.values()):,.2f}
"""
        self._send_message(update.effective_chat.id, text)

    async def cmd_trades(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /trades command."""
        if not self._check_admin(update):
            return

        from ..utils.db import Trade, get_session

        session = get_session()
        try:
            trades = (
                session.query(Trade)
                .filter(Trade.is_closed == True)
                .order_by(Trade.created_at.desc())
                .limit(10)
                .all()
            )

            if not trades:
                self._send_message(update.effective_chat.id, "No closed trades found.")
                return

            lines = ["*Recent Trades*"]
            for t in trades:
                pnl_str = f"${t.pnl:.2f}" if t.pnl else "N/A"
                lines.append(
                    f"  {t.pair} {t.side} | PnL: {pnl_str} | {t.outcome}"
                )

            self._send_message(update.effective_chat.id, "\n".join(lines))
        finally:
            session.close()

    async def cmd_pause(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /pause command."""
        if not self._check_admin(update):
            return

        from ..utils.db import SystemSetting, get_session

        session = get_session()
        try:
            setting = session.query(SystemSetting).filter_by(key="striker_paused").first()
            if setting:
                setting.value = "true"
            else:
                session.add(SystemSetting(key="striker_paused", value="true"))
            session.commit()
            self._send_message(
                update.effective_chat.id,
                "⏸ Striker module paused.",
            )
        finally:
            session.close()

    async def cmd_resume(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /resume command."""
        if not self._check_admin(update):
            return

        from ..utils.db import SystemSetting, get_session

        session = get_session()
        try:
            setting = session.query(SystemSetting).filter_by(key="striker_paused").first()
            if setting:
                setting.value = "false"
            session.commit()
            self._send_message(
                update.effective_chat.id,
                "▶ Striker module resumed.",
            )
        finally:
            session.close()

    async def cmd_scan(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /scan command."""
        if not self._check_admin(update):
            return

        from ..sleuth.onchain_scanner import OnChainScanner
        from ..sleuth.databroker_scanner import DataBrokerScanner

        self._send_message(
            update.effective_chat.id,
            "🔍 Starting scan...",
        )

        scanner = OnChainScanner.load()
        alerts = scanner.run_full_scan()

        broker_scanner = DataBrokerScanner.load()
        violations = broker_scanner.scan_all_brokers()

        text = f"""
✅ *Scan Complete*

  On-chain alerts: {len(alerts)}
  Broker violations: {len(violations)}
"""
        self._send_message(update.effective_chat.id, text)

    async def cmd_rebalance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /rebalance command."""
        if not self._check_admin(update):
            return

        from ..foundation.rebalancer import Rebalancer
        from ..utils.db import get_session

        session = get_session()
        try:
            self._send_message(
                update.effective_chat.id,
                "🔄 Starting rebalance...",
            )

            rebalancer = Rebalancer.load()
            trades = rebalancer.rebalance()

            session.commit()

            text = f"""
✅ *Rebalance Complete*

  Trades executed: {len(trades)}
"""
            self._send_message(update.effective_chat.id, text)
        finally:
            session.close()

    async def cmd_analyze(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /analyze command."""
        if not self._check_admin(update):
            return

        from ..intelligence.learning_loop import LearningLoop

        self._send_message(
            update.effective_chat.id,
            "🧠 Running learning analysis...",
        )

        loop = LearningLoop.load()
        result = loop.run_periodic_analysis()

        text = f"""
✅ *Analysis Complete*

  Trades analyzed: {result.get('trades_analyzed', 0)}
  Skill updates: {result.get('skill_updates_generated', 0)}
"""
        self._send_message(update.effective_chat.id, text)

    async def cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /help command."""
        if not self._check_admin(update):
            return

        lines = ["*Available Commands*"]
        for cmd, desc in self.commands.items():
            lines.append(f"/{cmd} — {desc}")

        self._send_message(update.effective_chat.id, "\n".join(lines))

    async def unknown_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle unknown commands."""
        await self.cmd_help(update, context)

    async def start(self) -> None:
        """Start the Telegram bot."""
        if not self.token:
            logger.warning("TELEGRAM_TOKEN not set. Telegram bot disabled.")
            return

        try:
            self.app = Application.builder().token(self.token).build()

            # Register handlers
            self.app.add_handler(CommandHandler("status", self.cmd_status))
            self.app.add_handler(CommandHandler("balance", self.cmd_balance))
            self.app.add_handler(CommandHandler("trades", self.cmd_trades))
            self.app.add_handler(CommandHandler("pause", self.cmd_pause))
            self.app.add_handler(CommandHandler("resume", self.cmd_resume))
            self.app.add_handler(CommandHandler("scan", self.cmd_scan))
            self.app.add_handler(CommandHandler("rebalance", self.cmd_rebalance))
            self.app.add_handler(CommandHandler("analyze", self.cmd_analyze))
            self.app.add_handler(CommandHandler("help", self.cmd_help))

            # Start polling
            self.app.run_polling()
            self.running = True
            logger.info("Telegram bot started.")

        except Exception as e:
            logger.error("Failed to start Telegram bot: %s", e)

    def stop(self) -> None:
        """Stop the Telegram bot."""
        if self.app:
            self.app.stop()
        self.running = False
        logger.info("Telegram bot stopped.")

    def _format_submissions(self, submissions: list, max_items: int = 5) -> str:
        """Format submissions list for display.

        Args:
            submissions: List of submission dicts.
            max_items: Maximum items to show.

        Returns:
            Formatted string.
        """
        if not submissions:
            return "  None yet."

        lines = []
        for sub in submissions[:max_items]:
            status_emoji = "✅" if sub.get("status") == "SENT" else "⏳"
            lines.append(
                f"  {status_emoji} {sub.get('target', '?')} "
                f"[{sub.get('status', '?')}]"
            )
        return "\n".join(lines)
