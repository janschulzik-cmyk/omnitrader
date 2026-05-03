#!/bin/bash
# Decrypt, run tests, re-encrypt.
# Usage: ./decrypt_for_tests.sh [--dry-run]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "CoreGuard Test Runner"
echo "=========================================="
echo ""

# Decrypt, run tests, re-encrypt
cd "$SCRIPT_DIR"
python3 decrypt_for_tests.py "$@"

exit $?
