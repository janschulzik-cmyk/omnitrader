#!/bin/bash
# Start mesh bootstrap node + phone-sim worker
# Usage: ./start_mesh_bootstrap.sh

set -e

MESH_DIR="/home/joe/ouroboros/Furyan/ai-mesh-main"
MESH_RELEASE="$MESH_DIR/target/release"

echo "=== Starting AI Mesh Bootstrap ==="

# Check binaries exist
if [ ! -f "$MESH_RELEASE/bootstrap-node" ]; then
    echo "ERROR: bootstrap-node binary not found. Running cargo build..."
    cd "$MESH_DIR"
    cargo build --release 2>&1 | tail -5
fi

# Start bootstrap node
echo "Starting bootstrap-node..."
$MESH_RELEASE/bootstrap-node --listen-port 9000 --bootstrap &
BOOTSTRAP_PID=$!
echo "Bootstrap node started (PID: $BOOTSTRAP_PID)"

# Wait for bootstrap to be ready
sleep 3

# Start ai-mesh (operator node)
echo "Starting ai-mesh operator node..."
$MESH_RELEASE/ai-mesh --listen-port 18081 --bootstrap-peer localhost:9000 &
AI_MESH_PID=$!
echo "AI Mesh operator started (PID: $AI_MESH_PID)"

# Start phone-sim worker
echo "Starting phone-sim worker..."
# Create a simple phone-sim script that sends signals to the mesh
cat > /tmp/mesh_phone_sim.py << 'PHONESIM'
import asyncio
import json
import time
import os
import sys

sys.path.insert(0, "/home/joe/ouroboros/Furyan/ai-mesh-main/src")
sys.path.insert(0, "/home/joe/ouroboros/cathedral/Omnitrader/src")

import httpx

MESH_API_URL = os.environ.get("MESH_API_URL", "http://localhost:18081")

async def main():
    print("Phone-sim worker: sending trade signals to mesh operator...")
    async with httpx.AsyncClient(base_url=MESH_API_URL, timeout=10) as client:
        while True:
            try:
                # Simulate a trade signal from phone
                signal = {
                    "pair": "SOL/USDT",
                    "action": "long",
                    "entry": 180.50,
                    "stop": 175.00,
                    "target": 190.00,
                    "source": "phone_sim",
                    "timestamp": time.time(),
                }
                resp = await client.post("/v1/operator/signal", json=signal)
                if resp.status_code == 200:
                    print(f"Signal sent: {signal}")
                else:
                    print(f"Failed to send signal: {resp.status_code}")
            except Exception as e:
                print(f"Error sending signal: {e}")

            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
PHONESIM

python3 /tmp/mesh_phone_sim.py &
PHONE_PID=$!
echo "Phone-sim worker started (PID: $PHONE_PID)"

echo ""
echo "=== Mesh Bootstrap Complete ==="
echo "Bootstrap PID: $BOOTSTRAP_PID"
echo "AI Mesh PID: $AI_MESH_PID"
echo "Phone-sim PID: $PHONE_PID"
echo ""
echo "Monitor with:"
echo "  curl -s http://localhost:18081/v1/operator/signals"
echo "  tail -f /tmp/ai-mesh.log"
