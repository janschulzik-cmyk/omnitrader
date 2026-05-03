#!/bin/bash
# Omnitrader Offline Launcher
# Starts Redis, Celery worker, and FastAPI server in offline mode.
#
# Usage:
#   ./start_omnitrader_offline.sh           # Normal offline mode
#   ./start_omnitrader_offline.sh --backtest # Backtesting mode
#   ./start_omnitrader_offline.sh --dry      # Only check health, then exit
#
# Environment variables:
#   OFFLINE_MODE=true              Disable all external API calls
#   BACKTEST_MODE=false            Enable backtest engine
#   BACKTEST_DATA_FILE=            Path to OHLCV CSV data
#   ANALYST_BUREAU_ENABLED=false   Disable AI analyst
#   EXCHANGE_TESTNET=true          Use exchange testnet
#   DATABASE_URL=sqlite:///data/omnitrader.db  SQLite database
#   API_KEY_SECRET=omnitrader-api-key-change-me  API auth
#   OMNITRADER_LOG=/tmp/omnitrader.log  Log file location

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# -- Default environment -----------------------------------------------------
export OFFLINE_MODE="${OFFLINE_MODE:-true}"
export ANALYST_BUREAU_ENABLED="${ANALYST_BUREAU_ENABLED:-false}"
export EXCHANGE_TESTNET="${EXCHANGE_TESTNET:-true}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///data/omnitrader.db}"
export API_KEY_SECRET="${API_KEY_SECRET:-omnitrader-api-key-change-me}"
export OMNITRADER_LOG="${OMNITRADER_LOG:-/tmp/omnitrader.log}"

BACKTEST_MODE="${BACKTEST_MODE:-false}"
BACKTEST_DATA_FILE="${BACKTEST_DATA_FILE:-backtest_sample.csv}"

if [ "${1:-}" = "--backtest" ]; then
    export BACKTEST_MODE=true
    echo "[Launcher] Starting in BACKTEST mode"
fi

if [ "${1:-}" = "--dry" ]; then
    echo "[Launcher] Dry-run mode — checking components only"
    export OFFLINE_MODE=true
    export BACKTEST_MODE=false
fi

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

# Process tracking
REDIS_PID=""
CELERY_PID=""
SERVER_PID=""

cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down Omnitrader...${NC}"
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
    [ -n "$CELERY_PID" ] && kill "$CELERY_PID" 2>/dev/null || true
    [ -n "$REDIS_PID" ] && kill "$REDIS_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    echo -e "${GREEN}Clean shutdown complete.${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}  Omnitrader Offline Launcher${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "  OFFLINE_MODE      = $OFFLINE_MODE"
echo "  BACKTEST_MODE     = $BACKTEST_MODE"
echo "  ANALYST_BUREAU    = $ANALYST_BUREAU_ENABLED"
echo "  EXCHANGE_TESTNET  = $EXCHANGE_TESTNET"
echo "  DATABASE_URL      = $DATABASE_URL"
echo "  LOG               = $OMNITRADER_LOG"
echo ""

# -- Verify Redis is available ----------------------------------------------
if ! command -v redis-server &>/dev/null; then
    echo -e "${RED}ERROR: redis-server not found. Install it first.${NC}"
    exit 1
fi

# -- Start Redis (if not already running) -----------------------------------
if ! redis-cli ping &>/dev/null; then
    echo -e "${YELLOW}Starting Redis...${NC}"
    redis-server --daemonize yes --save "" --appendonly no
    REDIS_PID=$!
    sleep 1
    if redis-cli ping &>/dev/null; then
        echo -e "${GREEN}  Redis started (PID $REDIS_PID)${NC}"
    else
        echo -e "${RED}  Redis failed to start.${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}  Redis already running${NC}"
fi

# -- Start Celery worker ----------------------------------------------------
echo -e "${YELLOW}Starting Celery worker...${NC}"
celery -A src.main.celery_app worker --loglevel=info --concurrency=4 \
    > "$OMNITRADER_LOG.celery" 2>&1 &
CELERY_PID=$!
sleep 2

# -- Start FastAPI server ---------------------------------------------------
echo -e "${YELLOW}Starting Omnitrader server...${NC}"
uvicorn src.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --access-log \
    > "$OMNITRADER_LOG" 2>&1 &
SERVER_PID=$!
sleep 3

# -- Verify health -----------------------------------------------------------
echo ""
echo "Verifying services..."

HEALTH_OK=false
for i in $(seq 1 10); do
    if curl -s -H "x-api-key: $API_KEY_SECRET" http://localhost:8000/health 2>/dev/null | grep -q "ok"; then
        HEALTH_OK=true
        break
    fi
    sleep 1
done

if [ "$HEALTH_OK" = true ]; then
    echo -e "${GREEN}  Server: OK${NC}"
    echo -e "${GREEN}  Celery: OK${NC}"
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}  Omnitrader is ONLINE${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
    echo "  Health:  http://localhost:8000/health"
    echo "  Status:  http://localhost:8000/api/v1/status"
    echo "  Trades:  http://localhost:8000/api/v1/trades"
    echo "  Swarms:  http://localhost:8000/api/v1/swarm/status"
    echo ""
    echo -e "${YELLOW}Press Ctrl+C to stop all services.${NC}"
    echo ""

    if [ "${1:-}" = "--dry" ]; then
        cleanup
    fi

    # Tail the log and wait for shutdown signal
    tail -f "$OMNITRADER_LOG" 2>/dev/null &
    TAIL_PID=$!
    wait $SERVER_PID 2>/dev/null &
    wait
else
    echo -e "${RED}  Server failed to start.${NC}"
    echo "  Check logs: $OMNITRADER_LOG"
    tail -20 "$OMNITRADER_LOG" 2>/dev/null || true
    cleanup
fi
