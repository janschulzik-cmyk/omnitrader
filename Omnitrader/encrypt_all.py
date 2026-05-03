#!/usr/bin/env python3
"""Extend CoreGuard encryption to all Omnitrader source files.

Walks src/ recursively and encrypts every .py file EXCEPT:
- __init__.py files
- Test files (in tests/ directory)
- Files already encrypted (.enc newer than .py)

Uses the same AES-256-GCM + HKDF-SHA256 method as apply_coreguard.py
with seed "hydra_seed_2026".

Usage:
    python3 encrypt_all.py [--dry-run]
"""
import os
import sys
import hashlib
import hmac
from pathlib import Path
from datetime import datetime

# ── Configuration ────────────────────────────────────────────────────

OMNITRADER_ROOT = Path(__file__).resolve().parent
ENTROPY_READ_SIZE = 32
KEY_SALT = b"ouroboros-coreguard-kdf-v1"
SEED = "hydra_seed_2026"
SRC_DIR = OMNITRADER_ROOT / "src"

# ── Encryption Functions ─────────────────────────────────────────────

def derive_key_from_seed(seed_str: str, label: bytes = b"payload-key") -> bytes:
    """Derive a deterministic 32-byte AES-256 key from a seed string."""
    state = seed_str.encode("utf-8")
    data = b""
    while len(data) < ENTROPY_READ_SIZE:
        state = hashlib.sha256(state + data).digest()
        data += state
    entropy = data[:ENTROPY_READ_SIZE]
    key = hkdf_sha256(entropy, length=32, salt=KEY_SALT, info=label)
    return key


def hkdf_sha256(ikm: bytes, length: int = 32, salt: bytes = b"", info: bytes = b"") -> bytes:
    """HKDF-SHA256 implementation (RFC 5869)."""
    if not salt:
        salt = b"\x00" * 32
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()

    t = b""
    output = b""
    counter = 1
    while len(output) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        output += t
        counter += 1

    return output[:length]


def aes256_gcm_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt with AES-256-GCM. Returns nonce(16) + ciphertext + tag(16)."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(key)
        # Deterministic nonce from file path for reproducibility
        nonce = hashlib.sha256(b"deterministic-nonce" + key).digest()[:16]
        ct = aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ct
    except ImportError:
        # XOR fallback
        print("[WARN] cryptography package not available. Using XOR fallback.", file=sys.stderr)
        nonce = hashlib.sha256(b"nonce-fallback" + key).digest()[:16]
        keystream = b""
        state = nonce
        while len(keystream) < len(plaintext):
            state = hashlib.sha256(state + key).digest()
            keystream += state
        ct = bytes(a ^ b for a, b in zip(plaintext, keystream[:len(plaintext)]))
        return nonce + ct


STUB_TEMPLATE = '''"""COREGUARD ENCRYPTED

This file is encrypted with Ouroboros CoreGuard AES-256-GCM.

To decrypt and execute, use:
    python3 /home/joe/ouroboros/cathedral/scripts/ouroboros_loader.py \\
        --encrypted {enc_path}.enc \\
        --key {key_hex}
"""

# WARNING: Original source has been encrypted by CoreGuard.
# The {enc_path}.enc file contains the AES-256-GCM encrypted payload.
# The .key file contains the hex-encoded decryption key.

import sys
import os

_enc_file = "{src_path}"
raise ImportError(
    f"CoreGuard encrypted module: {{_enc_file}}. "
    "Use ouroboros_loader.py --encrypted {enc_path}.enc --key {key_hex} to decrypt."
)
'''


def is_encryption_needed(py_file: Path) -> bool:
    """Check if encryption is needed (not already encrypted)."""
    enc_file = Path(str(py_file) + ".enc")
    key_file = Path(str(py_file) + ".key")

    # Already encrypted?
    if enc_file.exists() and key_file.exists():
        # Check if .enc is newer than .py (meaning already encrypted)
        try:
            if enc_file.stat().st_mtime > py_file.stat().st_mtime:
                return False
        except OSError:
            pass

    return True


def encrypt_file(py_file: Path, key: bytes) -> tuple:
    """Encrypt a file. Returns (encrypted_bytes, key_hex)."""
    plaintext = py_file.read_bytes()
    ciphertext = aes256_gcm_encrypt(plaintext, key)
    key_hex = key.hex()
    return ciphertext, key_hex


def main(dry_run: bool = False) -> int:
    """Walk src/ and encrypt all eligible .py files."""
    print("=" * 60)
    print("Ouroboros CoreGuard — Encrypt All Sources")
    print("=" * 60)
    print(f"\n[*] Seed: {SEED}")
    print(f"[*] Source root: {SRC_DIR}")

    # Collect all .py files to encrypt
    files_to_encrypt = []
    skipped = []

    for py_file in sorted(SRC_DIR.rglob("*.py")):
        # Skip __init__.py
        if py_file.name == "__init__.py":
            skipped.append(py_file)
            continue

        # Skip coreguard_hook — it must remain plaintext to install the
        # import hook before any encrypted module is loaded.
        if py_file.name == "coreguard_hook.py":
            skipped.append(py_file)
            continue

        # Skip test files
        if "tests" in py_file.parts:
            skipped.append(py_file)
            continue

        # Skip if not needed
        if not is_encryption_needed(py_file):
            skipped.append(py_file)
            continue

        files_to_encrypt.append(py_file)

    print(f"\n[*] Found {len(files_to_encrypt)} files to encrypt")
    print(f"[*] Skipped {len(skipped)} files (already encrypted, __init__.py, or test files)")

    if dry_run:
        print("\n[DRY RUN] No files modified.")
        for f in files_to_encrypt:
            print(f"  - {f.relative_to(OMNITRADER_ROOT)}")
        return 0

    # Encrypt each file
    results = []
    for py_file in files_to_encrypt:
        rel_path = str(py_file.relative_to(OMNITRADER_ROOT))
        key = derive_key_from_seed(SEED, f"payload-key-{rel_path}".encode())
        key_hex = key.hex()

        try:
            enc_file = Path(str(py_file) + ".enc")
            key_file = Path(str(py_file) + ".key")

            original_size = py_file.stat().st_size
            ciphertext, _ = encrypt_file(py_file, key)

            # Write encrypted payload
            enc_file.write_bytes(ciphertext)

            # Save key
            key_file.write_text(key_hex)

            # Replace original with stub
            stub = STUB_TEMPLATE.format(
                enc_path=str(py_file),
                key_hex=key_hex,
                src_path=str(py_file),
            )
            py_file.write_text(stub)

            results.append({
                "file": rel_path,
                "status": "OK",
                "original_size": original_size,
                "enc_size": len(ciphertext),
            })
            print(f"  [+] {rel_path} ({original_size} → {len(ciphertext)} bytes)")

        except Exception as e:
            results.append({
                "file": rel_path,
                "status": "FAIL",
                "error": str(e),
            })
            print(f"  [FAIL] {rel_path}: {e}")

    # Summary
    print(f"\n{'=' * 60}")
    ok = sum(1 for r in results if r["status"] == "OK")
    fail = sum(1 for r in results if r["status"] != "OK")
    print(f"Summary: {ok} encrypted, {fail} failed")
    print(f"{'=' * 60}\n")

    if fail > 0:
        print("Failed files:")
        for r in results:
            if r["status"] != "OK":
                print(f"  - {r['file']}: {r.get('error', 'unknown')}")
        return 1
    return 0


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    sys.exit(main(dry_run=dry_run))
