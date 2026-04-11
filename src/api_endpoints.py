"""API endpoint constants and request helpers for Bilibili."""

import httpx
from loguru import logger

# --- Base URLs ---
PASSPORT_BASE = "https://passport.bilibili.com"
API_BASE = "https://api.bilibili.com"
WWW_BASE = "https://www.bilibili.com"

# --- Auth endpoints ---
QR_GENERATE = f"{PASSPORT_BASE}/x/passport-login/web/qrcode/generate"
QR_POLL = f"{PASSPORT_BASE}/x/passport-login/web/qrcode/poll"
COOKIE_INFO = f"{PASSPORT_BASE}/x/passport-login/web/cookie/info"
COOKIE_REFRESH = f"{PASSPORT_BASE}/x/passport-login/web/cookie/refresh"
CONFIRM_REFRESH = f"{PASSPORT_BASE}/x/passport-login/web/confirm/refresh"

# --- Favorites endpoints ---
FAV_RESOURCE_LIST = f"{API_BASE}/x/v3/fav/resource/list"
FAV_RESOURCE_DEAL = f"{API_BASE}/x/v3/fav/resource/deal"
FAV_FOLDER_LIST_ALL = f"{API_BASE}/x/v3/fav/folder/created/list-all"

# --- Default headers (browser impersonation) ---
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# --- Bilibili RSA public key for cookie refresh correspondPath ---
BILIBILI_RSA_PUBLIC_KEY = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVmdomRMADWfKS0vf\n"
    "MElidUmaQByJRwVMVe7DJslth+sFgqecO1FDLMRW06sEnMHfiZT9MjNzIVhNHjhH\n"
    "nVB3D3mtaDYbE8p7Ym2CGpBsYCI3fSyHMmAu+xFN0Q0MAVmBtqoFIjsN0NNYhSlj\n"
    "5pR2UMGFmulti+bMYwIDAQAB\n"
    "-----END PUBLIC KEY-----"
)

# --- Known API error codes ---
CODE_SUCCESS = 0
CODE_ALREADY_EXISTS = 11007
CODE_RISK_CONTROL = -412
CODE_NOT_LOGGED_IN = -101
CODE_ACCESS_DENIED = -403
CODE_NETWORK_ERROR = -1  # sentinel for transport-level failures (no Bilibili response)


async def api_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    cookies: dict | None = None,
) -> dict:
    """Send GET request and return parsed JSON response.

    Network-level failures (disconnect, timeout, etc.) are caught and returned
    as {"code": CODE_NETWORK_ERROR, ...} so callers never see an unhandled exception.
    """
    try:
        resp = await client.get(url, params=params, cookies=cookies)
        data = resp.json()
        logger.debug("GET {} -> code={}", url, data.get("code"))
        return {"status_code": resp.status_code, **data}
    except httpx.TransportError as exc:
        logger.warning("GET {} network error: {}", url, exc)
        return {"code": CODE_NETWORK_ERROR, "message": str(exc), "status_code": 0}
    except Exception as exc:
        # Catches JSONDecodeError (HTML error page), UnicodeDecodeError, etc.
        logger.error("GET {} unexpected error: {}", url, exc)
        return {"code": CODE_NETWORK_ERROR, "message": str(exc), "status_code": 0}


async def api_post(
    client: httpx.AsyncClient,
    url: str,
    data: dict | None = None,
    cookies: dict | None = None,
) -> dict:
    """Send POST request (form-encoded) and return parsed JSON response.

    Network-level failures (disconnect, timeout, etc.) are caught and returned
    as {"code": CODE_NETWORK_ERROR, ...} so callers never see an unhandled exception.
    """
    try:
        resp = await client.post(
            url,
            data=data,
            cookies=cookies,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        result = resp.json()
        logger.debug("POST {} -> code={}", url, result.get("code"))
        return {"status_code": resp.status_code, **result}
    except httpx.TransportError as exc:
        logger.warning("POST {} network error: {}", url, exc)
        return {"code": CODE_NETWORK_ERROR, "message": str(exc), "status_code": 0}
    except Exception as exc:
        # Catches JSONDecodeError (HTML error page), UnicodeDecodeError, etc.
        logger.error("POST {} unexpected error: {}", url, exc)
        return {"code": CODE_NETWORK_ERROR, "message": str(exc), "status_code": 0}
