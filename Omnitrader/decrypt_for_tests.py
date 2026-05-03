#!/usr/bin/env python3
"""Decrypt all CoreGuard-encrypted source files for testing.

Workflow:
1. Backup original stub .py files
2. Decrypt all .enc files to temporary plaintext files
3. Replace stubs with decrypted content (so tests can import)
4. Run pytest
5. Re-encrypt with encrypt_all.py

Usage:
    python3 decrypt_for_tests.py [--dry-run]
"""
import os
import sys
import hashlib
import hmac
import shutil
from pathlib import Path

OMNITRADER_ROOT = Path(__file__).resolve().parent
SRC_DIR = OMNITRADER_ROOT / "src"
BACKUP_DIR = OMNITRADER_ROOT / ".coreguard_backup"
SEED = "hydra_seed_2026"
ENTROPY_READ_SIZE = 32
KEY_SALT = b"ouroboros-coreguard-kdf-v1"


def derive_key_from_seed(seed_str: str, label: bytes) -> bytes:
    state = seed_str.encode("utf-8")
    data = b""
    while len(data) < ENTROPY_READ_SIZE:
        state = hashlib.sha256(state + data).digest()
        data += state
    entropy = data[:ENTROPY_READ_SIZE]
    key = hkdf_sha256(entropy, length=32, salt=KEY_SALT, info=label)
    return key


def hkdf_sha256(ikm: bytes, length: int = 32, salt: bytes = b"", info: bytes = b"") -> bytes:
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


def aes256_gcm_decrypt(ciphertext_with_nonce: bytes, key: bytes) -> bytes:
    """Decrypt AES-256-GCM. Wire format: nonce(16) + ciphertext + tag(16)."""
    if len(ciphertext_with_nonce) < 32:
        raise ValueError("Payload too short")
    nonce = ciphertext_with_nonce[:16]
    ct = ciphertext_with_nonce[16:]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None)


def find_all_encrypted() -> list:
    """Find all .py files that have .enc and .key companions."""
    encrypted = []
    for enc_file in SRC_DIR.rglob("*.enc"):
        py_file = Path(str(enc_file).replace(".enc", ""))
        key_file = enc_file.with_suffix(".key")
        if py_file.exists() and key_file.exists():
            encrypted.append((py_file, enc_file, key_file))
    return encrypted


def decrypt_file(py_file: Path, enc_file: Path, key_file: Path) -> Path:
    """Decrypt an encrypted file and return path to plaintext backup."""
    key_hex = key_file.read_text().strip()
    key_bytes = bytes.fromhex(key_hex)

    ciphertext = enc_file.read_bytes()
    plaintext = aes256_gcm_decrypt(ciphertext, key_bytes)

    # Save to backup
    backup_file = BACKUP_DIR / str(py_file.relative_to(SRC_DIR))
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    backup_file.write_bytes(plaintext)

    # Replace stub with decrypted content
    py_file.write_bytes(plaintext)

    return backup_file


def encrypt_all():
    """Re-encrypt all source files."""
    script = OMNITRADER_ROOT / "encrypt_all.py"
    import subprocess
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=120
    )
    print(result.stdout)
    if result.returncode != 0:
        print("ERROR: Re-encryption failed!")
        print(result.stderr)
        return False
    return True


def main(dry_run: bool = False):
    print("=" * 60)
    print("CoreGuard Test Runner")
    print("=" * 60)

    encrypted = find_all_encrypted()
    if not encrypted:
        print("[*] No encrypted files found. Running tests normally.")
        return run_tests()

    print(f"\n[*] Found {len(encrypted)} encrypted files")

    if dry_run:
        print("[DRY RUN] Skipping decryption and re-encryption.")
        for py, enc, key in encrypted:
            print(f"  - {py.relative_to(OMNITRADER_ROOT)}")
        return run_tests()

    # Step 1: Backup stubs and decrypt
    print("\n[*] Step 1: Decrypting files for testing...")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    decrypted_count = 0
    for py_file, enc_file, key_file in encrypted:
        try:
            backup = decrypt_file(py_file, enc_file, key_file)
            print(f"  [+] Decrypted: {py_file.relative_to(OMNITRADER_ROOT)}")
            decrypted_count += 1
        except Exception as e:
            print(f"  [FAIL] {py_file}: {e}")
            sys.exit(1)

    # Step 2: Run tests
    print(f"\n[*] Step 2: Running tests ({decrypted_count} files decrypted)...")
    test_result = run_tests()

    # Step 3: Re-encrypt
    if test_result == 0:
        print(f"\n[*] Step 3: Re-encrypting {decrypted_count} files...")
        reencrypt_success = encrypt_all()
        if not reencrypt_success:
            print("[ERROR] Re-encryption failed! Files are left decrypted.")
            return 2
        print("[*] All files re-encrypted successfully.")
    else:
        print(f"\n[WARN] Tests failed. Files left decrypted for debugging.")

    print("=" * 60)
    print(f"Done. Test result: {'PASS' if test_result == 0 else 'FAIL'}")
    print("=" * 60)
    return test_result


def run_tests() -> int:
    """Run the pytest suite."""
    import subprocess
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "Omnitrader/tests/",
            "-v", "--tb=short",
            "-x",  # Stop on first failure
        ],
        cwd=str(OMNITRADER_ROOT.parent),
        capture_output=True,
        text=True,
        timeout=120,
    )

    # Print results
    print(result.stdout)
    if result.stderr:
        print(result.stderr[-500:])

    return result.returncode


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    sys.exit(main(dry_run=dry_run))
