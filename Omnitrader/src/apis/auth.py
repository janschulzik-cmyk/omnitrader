"""API Authentication for Omnitrader.

Provides API key-based authentication for the REST API endpoints
and Telegram bot authorization.
"""

import os
import hashlib
from functools import wraps
from typing import Optional

from fastapi import Header, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

# Current API key (read from environment)
API_KEY = os.environ.get("API_KEY", "")


def get_api_key() -> str:
    """Get the API key from environment.
    
    Checks API_KEY_SECRET first (primary), falls back to API_KEY.
    """
    return os.environ.get("API_KEY_SECRET", os.environ.get("API_KEY", ""))


def get_telegram_admin_id() -> int:
    """Get the Telegram admin user ID from environment."""
    return int(os.environ.get("TELEGRAM_ADMIN_ID", "0"))


def verify_api_key(authorization: Optional[str] = Header(None)) -> bool:
    """Verify the API key from the Authorization header.

    Args:
        authorization: Bearer token from header.

    Returns:
        True if valid.

    Raises:
        HTTPException: If authentication fails.
    """
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract token from "Bearer <token>"
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0] != "Bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication scheme",
        )

    token = parts[1]
    expected = get_api_key()

    if not expected or token != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
        )

    return True


def api_key_auth(credentials: Optional[HTTPAuthorizationCredentials] = Header(None),
                 x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """FastAPI dependency for API key authentication.
    
    Accepts either x-api-key header or Authorization: Bearer header.
    """
    # Try x-api-key header first
    if x_api_key:
        expected = get_api_key()
        if not expected or x_api_key != expected:
            raise HTTPException(
                status_code=401,
                detail="Invalid API key",
            )
        return x_api_key

    # Fall back to Authorization: Bearer
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Missing API key",
        )

    expected = get_api_key()
    if not expected or credentials.credentials != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
        )

    return credentials.credentials


def require_api_key(func):
    """Decorator for FastAPI routes that requires API key auth."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        auth = kwargs.get("credentials")
        if not auth:
            raise HTTPException(status_code=401, detail="API key required")
        return await func(*args, **kwargs)
    return wrapper


def verify_telegram_user(user_id: int) -> bool:
    """Verify the Telegram user is authorized.

    Args:
        user_id: Telegram user ID.

    Returns:
        True if the user is an admin.
    """
    admin_id = get_telegram_admin_id()
    return user_id == admin_id


def generate_api_key() -> str:
    """Generate a new random API key.

    Returns:
        Random API key string.
    """
    import secrets
    return secrets.token_hex(32)


def hash_api_key(api_key: str) -> str:
    """Hash an API key for storage (never store plaintext keys).

    Args:
        api_key: The API key to hash.

    Returns:
        SHA-256 hash of the key.
    """
    return hashlib.sha256(api_key.encode()).hexdigest()
