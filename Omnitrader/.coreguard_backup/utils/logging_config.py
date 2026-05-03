"""Logging configuration for Omnitrader.

Uses structlog for structured logging with file and console handlers.
All logs include request IDs, module names, and timestamps.
"""

import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional

import structlog

# Log directory
LOG_DIR = os.environ.get("LOG_DIR", "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_format: bool = True,
) -> structlog.BoundLogger:
    """Configure logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Optional path to log file. Defaults to logs/omnitrader.log.
        json_format: If True, use JSON formatting (for log aggregation).

    Returns:
        Configured structlog BoundLogger.
    """
    if log_file is None:
        log_file = os.path.join(LOG_DIR, "omnitrader.log")

    log_level = getattr(logging, level.upper(), logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # File handler with rotation
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level)

    # Structured logging with structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer() if json_format else structlog.dev.ConsoleRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for noisy_logger in (
        "websockets",
        "urllib3",
        "httpx",
        "ccxt",
        "faker",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    logger = structlog.get_logger()
    logger.info("Logging initialized", level=level, file=log_file, json=json_format)
    return logger


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger for a specific module.

    Args:
        name: Module name for the logger.

    Returns:
        BoundLogger instance for the module.
    """
    return structlog.get_logger(module=name)


class LogContext:
    """Context manager for adding structured context to log entries."""

    def __init__(self, **kwargs: Any):
        """Initialize with context key-value pairs."""
        self.context = kwargs

    def __enter__(self) -> "LogContext":
        import structlog.contextvars

        for key, value in self.context.items():
            structlog.contextvars.bind_contextvars(**{key: value})
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        import structlog.contextvars

        structlog.contextvars.clear_contextvars()


class LogEvent:
    """Generate unique log event IDs for tracking."""

    _counter = 0

    @classmethod
    def next_id(cls) -> str:
        """Generate a unique event ID.

        Returns:
            Unique event ID string (EVT-YYYYMMDD-HHMMSS-NNN).
        """
        cls._counter += 1
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        return f"EVT-{timestamp}-{cls._counter:04d}"

    @staticmethod
    def format_event_id(event_id: str) -> str:
        """Format an event ID for display.

        Args:
            event_id: The event ID string.

        Returns:
            Formatted event ID for display.
        """
        return f"[{event_id}]"
