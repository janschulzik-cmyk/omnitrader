"""Tests for APIs module (routes, Telegram bot, auth)."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio


class TestAPIRoutes:
    """Tests for FastAPI route endpoints."""

    @pytest.fixture
    def client(self):
        """Test client for FastAPI app."""
        from fastapi.testclient import TestClient
        from src.main import app
        return TestClient(app)

    def test_status_endpoint_requires_auth(self, client):
        """GET /status without API key returns 401."""
        response = client.get("/api/v1/status")
        assert response.status_code == 401

    def test_status_endpoint_with_api_key(self, client):
        """GET /status with valid API key returns 200."""
        with patch.dict("os.environ", {"API_KEY_SECRET": "test-api-key"}):
            response = client.get("/api/v1/status", headers={"X-API-Key": "test-api-key"})
            assert response.status_code == 200
            data = response.json()
            assert "pools" in data or "status" in data

    def test_command_endpoint_requires_auth(self, client):
        """POST /command requires API key."""
        response = client.post(
            "/api/v1/command",
            json={"command": "status"},
        )
        assert response.status_code == 401


class TestTelegramBot:
    """Tests for Telegram bot integration."""

    @pytest.fixture
    def bot(self):
        from src.apis.telegram_bot import TelegramBot
        with patch.dict("os.environ", {"TELEGRAM_TOKEN": "test-telegram-token"}):
            bot = TelegramBot(config={"token": "test-telegram-token", "chat_id": "123456"})
        return bot

    def test_init(self, bot):
        """Bot initializes with token from env."""
        assert bot.token == "test-telegram-token"
        assert bot.commands is not None
        assert "status" in bot.commands
        assert "pause" in bot.commands

    def test_send_message_via_app(self, bot):
        """Bot sends message through telegram app bot."""
        mock_bot = MagicMock()
        mock_app = MagicMock()
        mock_app.bot = mock_bot
        bot.app = mock_app
        bot._send_message(123456, "Hello")
        mock_bot.send_message.assert_called_once()

    def test_send_message_no_app(self, bot):
        """Bot gracefully handles missing app."""
        bot.app = None
        # Should not raise
        bot._send_message(123456, "Hello")

    def test_check_admin(self, bot):
        """Admin check delegates to verify_telegram_user."""
        mock_update = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 123456
        mock_update.effective_user = mock_user
        mock_chat = MagicMock()
        mock_chat.id = "123456"
        mock_update.effective_chat = mock_chat

        with patch.object(bot, "_send_message"):
            with patch("src.apis.telegram_bot.verify_telegram_user", return_value=True):
                assert bot._check_admin(mock_update) is True

            with patch("src.apis.telegram_bot.verify_telegram_user", return_value=False):
                assert bot._check_admin(mock_update) is False

    def test_cmd_status(self, bot):
        """Status command sends pool balances."""
        import asyncio
        async def _inner():
            mock_update = MagicMock()
            mock_chat = MagicMock()
            mock_chat.id = "123456"
            mock_update.effective_chat = mock_chat
            mock_context = MagicMock()

            with patch.object(bot, "_check_admin", return_value=True):
                with patch("src.hydra.Hydra") as MockHydra:
                    MockHydra.load.return_value.get_all_balances.return_value = {
                        "moat": 100.0, "striker": 70.0, "foundation": 20.0
                    }
                    with patch("src.sleuth.bounty_reporter.BountyReporter") as MockReporter:
                        MockReporter.load.return_value.get_submission_history.return_value = []
                        with patch("src.sleuth.databroker_scanner.DataBrokerScanner") as MockScanner:
                            MockScanner.load.return_value.get_scan_summary.return_value = {}
                            mock_bot = MagicMock()
                            mock_app = MagicMock()
                            mock_app.bot = mock_bot
                            bot.app = mock_app
                            await bot.cmd_status(mock_update, mock_context)
                            mock_bot.send_message.assert_called_once()
        asyncio.run(_inner())
    def test_cmd_pause(self, bot):
        """Pause command is handled."""
        import asyncio
        async def _inner():
            mock_update = MagicMock()
            mock_chat = MagicMock()
            mock_chat.id = "123456"
            mock_update.effective_chat = mock_chat
            mock_context = MagicMock()

            with patch.object(bot, "_check_admin", return_value=True):
                mock_session = MagicMock()
                mock_setting = MagicMock()
                mock_setting.value = "false"
                mock_session.query.return_value.filter_by.return_value.first.return_value = None

                with patch("src.utils.db.get_session", return_value=mock_session):
                    mock_bot = MagicMock()
                    mock_app = MagicMock()
                    mock_app.bot = mock_bot
                    bot.app = mock_app
                    await bot.cmd_pause(mock_update, mock_context)
                    mock_bot.send_message.assert_called_once()
                    # Verify DB was updated
                    mock_session.add.assert_called()
        asyncio.run(_inner())
    def test_cmd_resume(self, bot):
        """Resume command is handled."""
        import asyncio
        async def _inner():
            mock_update = MagicMock()
            mock_chat = MagicMock()
            mock_chat.id = "123456"
            mock_update.effective_chat = mock_chat
            mock_context = MagicMock()

            with patch.object(bot, "_check_admin", return_value=True):
                mock_session = MagicMock()
                mock_setting = MagicMock()
                mock_setting.value = "true"
                mock_session.query.return_value.filter_by.return_value.first.return_value = mock_setting

                with patch("src.utils.db.get_session", return_value=mock_session):
                    mock_bot = MagicMock()
                    mock_app = MagicMock()
                    mock_app.bot = mock_bot
                    bot.app = mock_app
                    await bot.cmd_resume(mock_update, mock_context)
                    mock_bot.send_message.assert_called_once()
                    assert mock_setting.value == "false"
        asyncio.run(_inner())
class TestAuth:
    """Tests for API authentication."""

    @patch("os.environ")
    def test_get_api_key_secret(self, mock_env):
        """API key reads API_KEY_SECRET first."""
        mock_env.get.side_effect = lambda key, default="": {
            "API_KEY_SECRET": "my-secret-key",
        }.get(key, default)
        from src.apis.auth import get_api_key
        assert get_api_key() == "my-secret-key"

    @patch("os.environ")
    def test_get_api_key_fallback(self, mock_env):
        """API key falls back to API_KEY when API_KEY_SECRET not set."""
        mock_env.get.side_effect = lambda key, default="": {
            "API_KEY": "my-secret-key",
        }.get(key, default)
        from src.apis.auth import get_api_key
        assert get_api_key() == "my-secret-key"

    @patch("os.environ")
    def test_get_api_key_empty(self, mock_env):
        """API key returns empty string when neither is set."""
        mock_env.get.side_effect = lambda key, default="": default
        from src.apis.auth import get_api_key
        assert get_api_key() == ""

    def test_verify_telegram_user(self):
        """Telegram user verification logic."""
        from src.apis.auth import verify_telegram_user
        with patch.dict("os.environ", {"TELEGRAM_ADMIN_ID": "123456"}):
            assert verify_telegram_user(123456) is True
            assert verify_telegram_user(99999) is False

    def test_generate_api_key(self):
        """API key generation produces a non-empty string."""
        from src.apis.auth import generate_api_key
        key = generate_api_key()
        assert isinstance(key, str)
        assert len(key) > 0
