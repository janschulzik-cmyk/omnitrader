"""Celery configuration for Omnitrader.

Defines task routing, scheduling, and serialization settings.
"""

import os
from celery.schedules import crontab

# Broker and backend
BROKER_URL = os.environ.get(
    "REDIS_URL",
    "redis://localhost:6379/0",
)
CELERY_RESULT_BACKEND = os.environ.get(
    "REDIS_URL",
    "redis://localhost:6379/1",
)

# Serialization
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

# Timezone
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True

# Task settings
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300  # 5 minutes hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 270  # 4.5 minutes soft limit

# Beat schedule — periodic task definitions
CELERY_BEAT_SCHEDULE = {
    # ── Striker tasks ──
    "news_monitor": {
        "task": "celery_app.news_monitor_task",
        "schedule": 900,  # every 15 minutes
    },
    "check_positions": {
        "task": "celery_app.check_positions_task",
        "schedule": 30,  # every 30 seconds
    },

    # ── Foundation tasks ──
    "politician_tracker": {
        "task": "celery_app.politician_tracker_task",
        "schedule": crontab(hour=9, minute=0),  # daily at 9am UTC
    },
    "dividend_portfolio_rebalance": {
        "task": "celery_app.dividend_rebalance_task",
        "schedule": crontab(hour=0, minute=0, day_of_week=0),  # weekly, Sunday 00:00 UTC
    },

    # ── Sleuth tasks ──
    "onchain_scan": {
        "task": "celery_app.onchain_scan_task",
        "schedule": 3600,  # every hour
    },
    "data_broker_scan": {
        "task": "celery_app.databroker_scan_task",
        "schedule": 86400,  # daily
    },
    "bounty_reporter_cleanup": {
        "task": "celery_app.bounty_cleanup_task",
        "schedule": 43200,  # every 12 hours
    },

    # ── Hydra tasks ──
    "reconcile_pools": {
        "task": "celery_app.reconcile_pools_task",
        "schedule": 86400,  # daily
    },

    # ── Intelligence tasks ──
    "learning_analysis": {
        "task": "celery_app.learning_analysis_task",
        "schedule": 604800,  # weekly
    },

    # ── Sentinel tasks ──
    "phish_domain_scan": {
        "task": "celery_app.phish_domain_scan_task",
        "schedule": 43200,  # every 12 hours
    },
    "credential_monitor_check": {
        "task": "celery_app.credential_monitor_check_task",
        "schedule": 60,  # every minute
    },
    "honeypot_log_rotate": {
        "task": "celery_app.honeypot_log_rotate_task",
        "schedule": 86400,  # daily
    },
}
