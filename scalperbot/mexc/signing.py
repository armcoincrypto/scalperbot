"""
MEXC API request signing.
"""

import hashlib
import hmac
import time
from typing import Dict, Optional
from urllib.parse import urlencode


def get_timestamp() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)


def create_signature(
    secret_key: str,
    params: Dict[str, any]
) -> str:
    """
    Create HMAC SHA256 signature for MEXC API.

    Args:
        secret_key: API secret key
        params: Request parameters (will be sorted)

    Returns:
        Hex-encoded signature string
    """
    # Sort parameters and create query string
    query_string = urlencode(sorted(params.items()))

    # Create HMAC SHA256 signature
    signature = hmac.new(
        secret_key.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return signature


def sign_params(
    api_key: str,
    secret_key: str,
    params: Optional[Dict[str, any]] = None,
    recv_window: int = 5000
) -> Dict[str, any]:
    """
    Sign request parameters for MEXC API.

    Args:
        api_key: API key
        secret_key: API secret
        params: Original parameters
        recv_window: Receive window in ms

    Returns:
        Parameters with timestamp and signature added
    """
    if params is None:
        params = {}

    # Add timestamp and recvWindow
    params["timestamp"] = get_timestamp()
    params["recvWindow"] = recv_window

    # Create signature
    signature = create_signature(secret_key, params)
    params["signature"] = signature

    return params


def get_auth_headers(api_key: str) -> Dict[str, str]:
    """
    Get authentication headers for MEXC API.

    Args:
        api_key: API key

    Returns:
        Headers dict with X-MEXC-APIKEY
    """
    return {
        "X-MEXC-APIKEY": api_key,
        "Content-Type": "application/json"
    }
