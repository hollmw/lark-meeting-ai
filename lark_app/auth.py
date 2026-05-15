"""
Lark Authentication
───────────────────
Manages tenant access tokens and user access tokens.
Tokens are cached and auto-refreshed before expiry.
"""

import time
import httpx
import yaml
from pathlib import Path
from pipeline._config import load_config


CONFIG = load_config()
APP_ID = CONFIG["lark"]["app_id"]
APP_SECRET = CONFIG["lark"]["app_secret"]

LARK_API_BASE = "https://open.larksuite.com/open-apis"

# ── Token cache ───────────────────────────────────────────────────────────────

_token_cache = {
    "tenant_access_token": None,
    "expires_at": 0,
}


# ── Tenant Access Token ───────────────────────────────────────────────────────

async def get_tenant_access_token() -> str:
    """
    Returns a valid tenant access token.
    Fetches a new one from Lark if expired or not yet retrieved.
    """
    now = time.time()

    # Return cached token if still valid (with 60s buffer)
    if _token_cache["tenant_access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["tenant_access_token"]

    # Fetch new token
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{LARK_API_BASE}/auth/v3/tenant_access_token/internal",
            json={
                "app_id": APP_ID,
                "app_secret": APP_SECRET,
            },
        )
        response.raise_for_status()
        data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(f"Failed to get tenant access token: {data.get('msg')}")

    token = data["tenant_access_token"]
    expires_in = data.get("expire", 7200)

    # Cache token
    _token_cache["tenant_access_token"] = token
    _token_cache["expires_at"] = now + expires_in

    return token


# ── Auth Headers ──────────────────────────────────────────────────────────────

async def get_auth_headers() -> dict:
    """Returns authorization headers for Lark API calls."""
    token = await get_tenant_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ── Verify Webhook Signature ──────────────────────────────────────────────────

def verify_lark_request(token: str) -> bool:
    """
    Verifies that incoming webhook requests are genuinely from Lark.
    Compares the token against the verification token in config.
    """
    expected = CONFIG["lark"]["verification_token"]
    return token == expected
