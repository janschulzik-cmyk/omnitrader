# Omnitrader: Autonomous Multi-Axis Wealth Engine

## Overview

Omnitrader is a Python-based autonomous agent system that runs 24/7 on a cloud Linux server. It consists of three independent but interacting modules:

- **Striker** – event-driven mean-reversion trader that exploits media overreactions
- **Foundation** – long-term diversified portfolio manager tracking congressional trades, high-dividend assets, and DeFi yield
- **Sleuth** – on-chain investigator that submits evidence to bounty programs (legal whistleblowing)

A capital allocation layer (the *Hydra*) manages three virtual pools: **Moat** (safe cash), **StrikerPool**, and **FoundationPool**. A Celery task scheduler orchestrates all periodic jobs.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Omnitrader System                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │  Striker │  │Foundation│  │  Sleuth  │                  │
│  │          │  │          │  │          │                  │
│  │ NewsAPI  │  │Congress  │  │On-chain  │                  │
│  │ ccxt     │  │Dividends │  │Bounties  │                  │
│  │ MeanRev  │  │DeFi      │  │Scanner   │                  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                  │
│       │             │             │                          │
│       ▼             ▼             ▼                          │
│  ┌─────────────────────────────────────┐                    │
│  │           Hydra (Capital Pools)      │                    │
│  │   Moat (10%) │ Striker (70%) │ Found │                    │
│  └─────────────────────────────────────┘                    │
│       │                                                      │
│       ▼                                                      │
│  ┌─────────────────────────────────────┐                    │
│  │     Intelligence & Learning Loop     │                    │
│  │  LLM Analysis → Skill File Updates  │                    │
│  └─────────────────────────────────────┘                    │
│       │                                                      │
│       ▼                                                      │
│  ┌─────────────────────────────────────┐                    │
│  │       FastAPI + Telegram Bot        │                    │
│  │  /status  /command  /balance        │                    │
│  └─────────────────────────────────────┘                    │
│                                                             │
│  ┌─────────────────────────────────────┐                    │
│  │         Celery Scheduler             │                    │
│  │  Periodic task orchestration        │                    │
│  └─────────────────────────────────────┘                    │
│                                                             │
│  ┌─────────────────────────────────────┐                    │
│  │      SQLite Database + Redis         │                    │
│  │  Trade logs, balances, events       │                    │
│  └─────────────────────────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
Omnitrader/
├── README.md
├── .env
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── config/
│   ├── settings.yaml
│   └── skills/
│       ├── striker_skill.txt
│       ├── foundation_skill.txt
│       └── sleuth_skill.txt
├── src/
│   ├── main.py
│   ├── hydra.py
│   ├── striker/
│   │   ├── __init__.py
│   │   ├── news_monitor.py
│   │   ├── trade_executor.py
│   │   └── mean_reversion.py
│   ├── foundation/
│   │   ├── __init__.py
│   │   ├── politician_tracker.py
│   │   ├── dividend_portfolio.py
│   │   └── rebalancer.py
│   ├── sleuth/
│   │   ├── __init__.py
│   │   ├── onchain_scanner.py
│   │   └── bounty_reporter.py
│   ├── intelligence/
│   │   ├── __init__.py
│   │   ├── learning_loop.py
│   │   └── llm_interface.py
│   ├── apis/
│   │   ├── __init__.py
│   │   ├── routes.py
│   │   ├── telegram_bot.py
│   │   └── auth.py
│   └── utils/
│       ├── __init__.py
│       ├── logging_config.py
│       ├── db.py
│       └── security.py
├── .github/workflows/
│   └── deploy.yml
└── tests/
    └── test_*.py
```

## Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Redis 7+
- Exchange API keys (Binance, Kraken)
- NewsAPI.org API key
- Telegram Bot token

## Quick Start (Docker)

1. Clone the repository:
```bash
git clone <repo-url>
cd Omnitrader
```

2. Copy the environment file and configure:
```bash
cp .env.example .env
# Edit .env with your API keys and settings
```

3. Build and start:
```bash
docker-compose up --build -d
```

4. Check status:
```bash
docker-compose ps
```

5. View logs:
```bash
docker-compose logs -f web
```

## Local Development

1. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment:
```bash
cp .env.example .env
# Edit .env with your API keys
```

4. Start Redis (or use Docker):
```bash
docker run -d -p 6379:6379 --name omnitrader-redis redis:7-alpine
```

5. Run the application:
```bash
python -m src.main
```

6. Run in development mode:
```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

## Configuration

All configuration is in `config/settings.yaml`. Key variables:

### Exchange
| Variable | Description | Default |
|----------|-------------|---------|
| exchange.name | Exchange to connect to | binance |
| exchange.testnet | Use testnet | true |
| exchange.api_key_env | Env var for API key | EXCHANGE_API_KEY |

### Risk Management
| Variable | Description | Default |
|----------|-------------|---------|
| risk.risk_per_trade | % of pool per trade | 0.02 |
| risk.reward_risk_ratio | Reward-to-risk ratio | 2.0 |
| risk.max_concurrent_trades | Max open positions | 3 |

### Capital Pools
| Variable | Description | Default |
|----------|-------------|---------|
| capital.moat_ratio | Moat % of total | 0.1 |
| capital.foundation_ratio | Foundation % of total | 0.2 |
| capital.striker_ratio | Striker % of total | 0.7 |
| capital.profit_split.striker_keep | Striker profit share | 0.5 |
| capital.profit_split.moat | Moat profit share | 0.25 |
| capital.profit_split.foundation | Foundation profit share | 0.25 |

### Telegram
| Variable | Description |
|----------|-------------|
| telegram.bot_token_env | Env var for bot token |
| telegram.chat_id_env | Env var for chat ID |

### LLM
| Variable | Description |
|----------|-------------|
| llm.model | LLM model (OpenRouter) |
| llm.base_url | API endpoint |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /status | All pool balances, open trades, latest bounties |
| POST | /command | Natural language command execution |
| GET | /health | Health check |
| GET | /trades | List all trades |
| GET | /striker/status | Striker module status |
| GET | /foundation/status | Foundation module status |
| GET | /sleuth/status | Sleuth module status |

## Celery Tasks

| Task Name | Schedule | Description |
|-----------|----------|-------------|
| striker.poll_news | Every 15 min | Fetch and analyze news headlines |
| striker.check_positions | Every 30 sec | Monitor open positions |
| foundation.poll_congress | Daily 9 AM UTC | Check congressional trades |
| foundation.rebalance | Weekly | Rebalance portfolio weights |
| sleuth.scan_blockchain | Every 6 hours | Scan for suspicious activity |
| intelligence.learn | Daily | Analyze trades, generate skill updates |
| hydra.reconcile | Daily | Sync DB balances with exchange |
| hydra.moat_update | Daily | Update Moat balance from blockchain |

## Testing

Run the test suite:
```bash
pytest tests/ -v
```

Run integration tests (with Binance testnet):
```bash
pytest tests/ -v --integration
```

## Logging

Logs are written to `logs/omnitrader.log`. Configuration is in `src/utils/logging_config.py`.

Log levels:
- DEBUG: Detailed debugging information
- INFO: General operational messages
- WARNING: Warning conditions
- ERROR: Error conditions
- CRITICAL: Critical failures

## Security

- Exchange API keys are encrypted at rest using the master passphrase from `ENCRYPTION_MASTER_PASSPHRASE`
- DeFi wallet private keys are stored only in environment variables; never written to disk
- SQLite database contains only non-sensitive operational data
- All external API calls use HTTPS
- No data is posted to social media
- Bounty reports are sent via encrypted email only

## Deployment

### Docker Compose (Recommended)
```bash
docker-compose up -d
```

### Systemd Service
```bash
sudo cp systemd/omnitrader.service /etc/systemd/system/
sudo systemctl enable omnitrader
sudo systemctl start omnitrader
```

### Manual Deployment
1. Set up Python 3.11 environment
2. Install dependencies from requirements.txt
3. Configure .env file
4. Start Redis
5. Run: `celery -A src.main worker --loglevel=info --concurrency=4 &`
6. Run: `uvicorn src.main:app --host 0.0.0.0 --port 8000 &`

## License

Internal use only. Do not distribute.
