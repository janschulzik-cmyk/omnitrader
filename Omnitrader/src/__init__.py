"""Omnitrader — Autonomous Multi-Axis Wealth Engine."""

# ── CoreGuard import hook ───────────────────────────────────────────
# Import and auto-install before any other imports.
# The coreguard_hook module installs a sys.meta_path finder that
# intercepts and decrypts encrypted .py files on-the-fly.
import src.coreguard_hook  # noqa: F401 — side-effect: installs hook
