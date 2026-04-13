"""Dual-account QR code login and cookie refresh for Bilibili."""

import asyncio
import base64
import binascii
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import qrcode
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from loguru import logger

from src.api_endpoints import (
    BILIBILI_RSA_PUBLIC_KEY,
    CODE_NOT_LOGGED_IN,
    CONFIRM_REFRESH,
    COOKIE_INFO,
    COOKIE_REFRESH,
    QR_GENERATE,
    QR_POLL,
    WWW_BASE,
    api_get,
    api_post,
)
from src.config import (
    build_cookie_string,
    get_cookie_dict,
    load_config,
    save_config,
)


class CookieExpiredError(Exception):
    """Raised when a cookie is expired or has been force-invalidated by Bilibili."""


async def generate_qr(client: httpx.AsyncClient) -> tuple[str, str]:
    """Request a new QR code for login.

    Returns:
        (qrcode_key, login_url) tuple
    """
    result = await api_get(client, QR_GENERATE)
    data = result.get("data", {})
    qrcode_key = data["qrcode_key"]
    login_url = data["url"]
    logger.info("QR code generated, key={}", qrcode_key[:8] + "...")
    return qrcode_key, login_url


def display_qr_terminal(url: str) -> None:
    """Print QR code as ASCII art in the terminal."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def _extract_cookies_from_url(url: str) -> dict:
    """Extract SESSDATA, bili_jct, DedeUserID from the redirect URL query params."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    result = {}
    for key in ("SESSDATA", "bili_jct", "DedeUserID"):
        values = params.get(key, [])
        if values:
            result[key] = values[0]
    return result


async def poll_qr_login(
    client: httpx.AsyncClient,
    qrcode_key: str,
    timeout: float = 120.0,
    poll_interval: float = 2.0,
) -> dict:
    """Poll QR code login status until success or timeout.

    Args:
        client: HTTP client
        qrcode_key: Key from generate_qr
        timeout: Max seconds to wait (prevents infinite blocking)
        poll_interval: Seconds between polls

    Returns:
        Dict with sessdata, bili_jct, dede_user_id, refresh_token

    Raises:
        asyncio.TimeoutError: If login not completed within timeout
        RuntimeError: If QR code expires
    """

    async def _poll_loop() -> dict:
        while True:
            resp = await client.get(QR_POLL, params={"qrcode_key": qrcode_key})
            result = resp.json()
            data = result.get("data", {})
            code = data.get("code", -1)

            if code == 0:
                # Login success - extract cookies from URL in response
                redirect_url = data.get("url", "")
                cookies = _extract_cookies_from_url(redirect_url)
                refresh_token = data.get("refresh_token", "")

                # Also try to get cookies from Set-Cookie headers
                for key in ("SESSDATA", "bili_jct", "DedeUserID"):
                    if key not in cookies and key.lower() in resp.cookies:
                        cookies[key] = resp.cookies[key.lower()]
                    elif key not in cookies:
                        header_val = resp.cookies.get(key)
                        if header_val:
                            cookies[key] = header_val

                logger.info("QR login successful, user={}", cookies.get("DedeUserID", "?"))
                return {
                    "sessdata": cookies.get("SESSDATA", ""),
                    "bili_jct": cookies.get("bili_jct", ""),
                    "dede_user_id": cookies.get("DedeUserID", ""),
                    "refresh_token": refresh_token,
                }
            elif code == 86101:
                logger.debug("QR not scanned yet...")
            elif code == 86090:
                logger.info("QR scanned, waiting for confirmation...")
            elif code == 86038:
                raise RuntimeError("QR code expired, please re-generate")
            else:
                logger.warning("Unknown QR poll code: {}", code)

            await asyncio.sleep(poll_interval)

    return await asyncio.wait_for(_poll_loop(), timeout=timeout)


async def login_account(
    client: httpx.AsyncClient,
    account_label: str,
    timeout: float = 120.0,
) -> dict:
    """Complete login flow for one account.

    Args:
        client: HTTP client
        account_label: Display name (e.g., "主账号" or "副账号")
        timeout: Max seconds to wait for scan

    Returns:
        Dict with sessdata, bili_jct, dede_user_id, refresh_token
    """
    qrcode_key, login_url = await generate_qr(client)

    print(f"\n{'='*50}")
    print(f"  请使用 B站App 扫描以下二维码登录【{account_label}】")
    print(f"{'='*50}")
    display_qr_terminal(login_url)
    print(f"  等待扫码中... (超时时间: {timeout}秒)")
    print(f"{'='*50}\n")

    credentials = await poll_qr_login(client, qrcode_key, timeout=timeout)
    logger.info("【{}】登录成功", account_label)
    return credentials


def _credentials_to_config_entry(credentials: dict, media_id: str = "") -> dict:
    """Convert login credentials to config.json account entry."""
    cookie_dict = {
        "SESSDATA": credentials["sessdata"],
        "bili_jct": credentials["bili_jct"],
        "DedeUserID": credentials["dede_user_id"],
    }
    cookie_str = build_cookie_string(cookie_dict)
    return {
        "cookie": cookie_str,
        "refresh_token": credentials["refresh_token"],
    }


async def check_cookie_needs_refresh(
    client: httpx.AsyncClient,
    cookies: dict,
) -> tuple[bool, int]:
    """Check if cookies need refreshing.

    Returns:
        (needs_refresh, timestamp) tuple

    Raises:
        CookieExpiredError: If the cookie is expired or force-invalidated (-101).
    """
    result = await api_get(client, COOKIE_INFO, cookies=cookies)
    if result.get("code") == CODE_NOT_LOGGED_IN:
        raise CookieExpiredError("Cookie is expired or has been invalidated (code -101)")
    data = result.get("data", {})
    needs_refresh = data.get("refresh", False)
    timestamp = data.get("timestamp", 0)
    if needs_refresh:
        logger.info("Cookie needs refresh (timestamp={})", timestamp)
    return needs_refresh, timestamp


def generate_correspond_path() -> str:
    """Generate correspondPath by RSA-OAEP encrypting 'refresh_{timestamp}'.

    Uses current millisecond timestamp and hex encoding, per bilibili-api spec.
    """
    ts = round(time.time() * 1000)
    message = f"refresh_{ts}".encode("utf-8")
    public_key = serialization.load_pem_public_key(
        BILIBILI_RSA_PUBLIC_KEY.encode("utf-8")
    )
    encrypted = public_key.encrypt(
        message,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return binascii.b2a_hex(encrypted).decode()


async def get_refresh_csrf(
    client: httpx.AsyncClient,
    correspond_path: str,
    cookies: dict,
) -> str:
    """Fetch refresh_csrf from Bilibili correspond page.

    Args:
        client: HTTP client
        correspond_path: Hex string from generate_correspond_path
        cookies: Current account cookies

    Returns:
        The refresh_csrf token string
    """
    url = f"{WWW_BASE}/correspond/1/{correspond_path}"
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    resp = await client.get(url, headers={"Cookie": cookie_header})
    html = resp.text
    # Extract refresh_csrf from HTML using regex
    logger.debug("correspond page status={}, body[:500]={}", resp.status_code, html[:500])
    match = re.search(r'<div\s+id="1-name">([^<]+)</div>', html)
    if not match:
        raise RuntimeError("Failed to extract refresh_csrf from correspond page")
    csrf = match.group(1).strip()
    logger.debug("Got refresh_csrf: {}...", csrf[:8])
    return csrf


async def refresh_cookie(
    client: httpx.AsyncClient,
    account_key: str,
    config: dict,
    config_path: str = "config.json",
) -> dict:
    """Full cookie refresh flow for one account.

    1. Check if refresh needed (raises CookieExpiredError if cookie is dead)
    2. Record last_cookie_check timestamp regardless of whether refresh was needed
    3. If refresh needed: generate correspondPath, get refresh_csrf, POST refresh, confirm
    4. Overwrite config.json immediately

    Returns:
        Updated config dict (always includes updated last_cookie_check)

    Raises:
        CookieExpiredError: If the cookie is expired or force-invalidated.
    """
    cookies = get_cookie_dict(account_key, config)
    needs_refresh, timestamp = await check_cookie_needs_refresh(client, cookies)
    # CookieExpiredError propagates up — do NOT update last_cookie_check in that case

    # Record that we successfully checked (regardless of whether refresh was needed)
    now_str = datetime.now(timezone.utc).isoformat()
    updated_config = {
        **config,
        account_key: {**config[account_key], "last_cookie_check": now_str},
    }

    if not needs_refresh:
        save_config(updated_config, config_path)
        logger.info("【{}】Cookie is still valid, no refresh needed", account_key)
        return updated_config

    # Generate correspondPath
    correspond_path = generate_correspond_path()

    # Get refresh_csrf
    refresh_csrf = await get_refresh_csrf(client, correspond_path, cookies)

    # Perform refresh
    old_csrf = cookies.get("bili_jct", "")
    refresh_token = config[account_key].get("refresh_token", "")

    result = await api_post(
        client,
        COOKIE_REFRESH,
        data={
            "csrf": old_csrf,
            "refresh_csrf": refresh_csrf,
            "source": "main_web",
            "refresh_token": refresh_token,
        },
        cookies=cookies,
    )

    if result.get("code") != 0:
        raise RuntimeError(
            f"Cookie refresh failed for {account_key}: "
            f"code={result.get('code')}, msg={result.get('message')}"
        )

    # Extract new tokens from response
    new_data = result.get("data", {})
    new_refresh_token = new_data.get("refresh_token", refresh_token)

    # Extract new cookies from response (the POST response sets new cookies)
    new_cookie_dict = {**cookies}
    token_info = new_data.get("token", {})
    if token_info:
        new_cookie_dict["SESSDATA"] = token_info.get("SESSDATA", cookies.get("SESSDATA", ""))
        new_cookie_dict["bili_jct"] = token_info.get("bili_jct", cookies.get("bili_jct", ""))

    # Confirm the refresh (invalidate old refresh_token)
    await api_post(
        client,
        CONFIRM_REFRESH,
        data={
            "csrf": new_cookie_dict.get("bili_jct", ""),
            "refresh_token": refresh_token,  # old refresh token
        },
        cookies=new_cookie_dict,
    )

    # Update config with new credentials and check timestamp
    updated_config[account_key] = {
        **updated_config[account_key],
        "cookie": build_cookie_string(new_cookie_dict),
        "refresh_token": new_refresh_token,
    }
    save_config(updated_config, config_path)
    logger.info("【{}】Cookie refreshed and saved", account_key)

    return updated_config
