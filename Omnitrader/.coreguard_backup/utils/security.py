"""Security utilities for Omnitrader.

Provides encryption for sensitive data (API keys, wallet keys),
key derivation, and nonce generation.
"""

import os
import secrets
import hashlib
import hmac
import json
from base64 import b64encode, b64decode
from datetime import datetime
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .logging_config import get_logger

logger = get_logger("security")

# Encryption key from environment or generate from master passphrase
MASTER_PASSPHRASE = os.environ.get("EXCHANGE_MASTER_PASSPHRACE", "change_this_master_passphrase_32_chars!")
DB_KEY_ENV = "OMNITRADER_DB_ENCRYPTION_KEY"

# Constant salt for PBKDF2 key derivation (must match whatever was used to encrypt stored secrets)
KEY_DERIVATION_SALT = os.environ.get("EXCHANGE_KEY_DERIVATION_SALT", "omnitrader_salt_2024_v1")

# PBKDF2 iterations (OWASP 2024 recommendation)
ITERATIONS = 480_000


def derive_key(passphrase: str = None, salt: bytes = None) -> bytes:
    """Derive a 32-byte Fernet key from passphrase using PBKDF2.

    Args:
        passphrase: Master passphrase. Defaults to MASTER_PASSPHRASE.
        salt: Salt bytes. Defaults to KEY_DERIVATION_SALT bytes.

    Returns:
        32-byte derived key.
    """
    if passphrase is None:
        passphrase = MASTER_PASSPHRASE
    if salt is None:
        salt = KEY_DERIVATION_SALT.encode("utf-8")
    return hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        ITERATIONS,
        dklen=32,
    )


def get_fernet(passphrase: str = None) -> Fernet:
    """Get a Fernet cipher instance for encryption/decryption.

    Args:
        passphrase: Master passphrase. Defaults to env var.

    Returns:
        Fernet cipher instance.
    """
    key = derive_key(passphrase)
    # Fernet requires a 32-byte key encoded as URL-safe base64
    fernet_key = b64encode(key).decode("utf-8")
    return Fernet(fernet_key)


def encrypt_string(plaintext: str, passphrase: str = None) -> str:
    """Encrypt a string using Fernet symmetric encryption.

    Args:
        plaintext: The string to encrypt.
        passphrase: Master passphrase. Defaults to env var.

    Returns:
        Base64-encoded encrypted string (includes timestamp and IV).
    """
    if not plaintext:
        return ""

    fernet = get_fernet(passphrase)
    timestamp = datetime.utcnow().isoformat()
    payload = json.dumps({
        "plaintext": plaintext,
        "timestamp": timestamp,
    })
    encrypted = fernet.encrypt(payload.encode("utf-8"))
    return b64encode(encrypted).decode("utf-8")


def decrypt_string(encrypted_b64: str, passphrase: str = None) -> str:
    """Decrypt an encrypted string.

    Args:
        encrypted_b64: Base64-encoded encrypted string.
        passphrase: Master passphrase. Defaults to env var.

    Returns:
        Decrypted plaintext string.
    """
    if not encrypted_b64:
        return ""

    try:
        fernet = get_fernet(passphrase)
        encrypted = b64decode(encrypted_b64.encode("utf-8"))
        payload = fernet.decrypt(encrypted)
        data = json.loads(payload.decode("utf-8"))
        return data.get("plaintext", "")
    except Exception as e:
        logger.error("Decryption failed: %s", e)
        raise ValueError("Failed to decrypt: invalid ciphertext or passphrase")


def encrypt_dict(data: Dict[str, str], passphrase: str = None) -> str:
    """Encrypt a dictionary as JSON, then encrypt the result.

    Args:
        data: Dictionary of secret key-value pairs.
        passphrase: Master passphrase. Defaults to env var.

    Returns:
        Base64-encoded encrypted JSON string.
    """
    json_str = json.dumps(data)
    return encrypt_string(json_str, passphrase)


def decrypt_dict(encrypted_b64: str, passphrase: str = None) -> Dict[str, str]:
    """Decrypt an encrypted JSON dictionary.

    Args:
        encrypted_b64: Base64-encoded encrypted JSON string.
        passphrase: Master passphrase. Defaults to env var.

    Returns:
        Decrypted dictionary.
    """
    json_str = decrypt_string(encrypted_b64, passphrase)
    return json.loads(json_str)


def get_encrypted_exchange_keys() -> Dict[str, Optional[str]]:
    """Load and decrypt exchange API keys from environment.

    Returns:
        Dict with 'api_key' and 'api_secret'. Keys are None if not configured.
    """
    encrypted_key = os.environ.get("EXCHANGE_API_KEY", "")
    encrypted_secret = os.environ.get("EXCHANGE_API_SECRET", "")

    result = {
        "api_key": None,
        "api_secret": None,
    }

    if encrypted_key:
        try:
            result["api_key"] = decrypt_string(encrypted_key)
        except Exception as e:
            logger.debug("Key decryption failed (%s), treating as plain text", e)
            result["api_key"] = encrypted_key

    if encrypted_secret:
        try:
            result["api_secret"] = decrypt_string(encrypted_secret)
        except Exception as e:
            logger.debug("Secret decryption failed (%s), treating as plain text", e)
            result["api_secret"] = encrypted_secret

    logger.info("Exchange keys loaded (encrypted=%s, present=%s)",
                bool(encrypted_key), bool(result["api_key"]))
    return result


def get_wallet_private_key(wallet_name: str = "dao") -> str:
    """Retrieve a wallet private key from environment.

    NOTE: Private keys are stored unencrypted in .env and loaded at runtime.
    They are never written to disk or logs.

    Args:
        wallet_name: Which wallet to get ("dao", "moat", "striker").

    Returns:
        Wallet private key string.
    """
    env_var_map = {
        "dao": "DAO_WALLET_PRIVATE_KEY",
        "moat": "MOAT_WALLET_PRIVATE_KEY",
        "striker": "STRiker_WALLET_PRIVATE_KEY",
    }
    env_var = env_var_map.get(wallet_name, "DAO_WALLET_PRIVATE_KEY")
    key = os.environ.get(env_var, "")
    if not key:
        logger.warning("No private key found for wallet '%s' (env: %s)", wallet_name, env_var)
    return key


def get_wallet_address(wallet_name: str = "dao") -> str:
    """Retrieve a wallet address from environment.

    Args:
        wallet_name: Which wallet to get.

    Returns:
        Wallet address string.
    """
    env_var_map = {
        "dao": "DAO_WALLET_ADDRESS",
        "moat": "MOAT_WALLET_ADDRESS",
        "striker": "STRiker_WALLET_ADDRESS",
    }
    env_var = env_var_map.get(wallet_name, "DAO_WALLET_ADDRESS")
    return os.environ.get(env_var, "")


def generate_nonce() -> str:
    """Generate a cryptographically secure nonce.

    Returns:
        Hex-encoded 32-byte nonce.
    """
    return secrets.token_hex(32)


def generate_api_key() -> str:
    """Generate a secure API key for Omnitrader authentication.

    Returns:
        Random hex string suitable as an API key.
    """
    return secrets.token_hex(32)


def verify_hmac_signature(data: str, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature.

    Args:
        data: The data that was signed.
        signature: The HMAC signature to verify.
        secret: The shared secret.

    Returns:
        True if signature matches.
    """
    expected = hmac.new(
        secret.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    """Mask a sensitive string for display in logs.

    Args:
        value: The string to mask.
        visible_chars: Number of characters to show at start.

    Returns:
        Masked string (e.g., "abcd****").
    """
    if not value or len(value) <= visible_chars:
        return "*" * max(len(value), 8)
    return value[:visible_chars] + "*" * (len(value) - visible_chars)
