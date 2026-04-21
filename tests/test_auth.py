"""Tests for auth module - QR login and cookie refresh."""

import asyncio

import httpx
import pytest
import respx

from src.api_endpoints import (
    COOKIE_INFO,
    COOKIE_REFRESH,
    CONFIRM_REFRESH,
    QR_GENERATE,
    QR_POLL,
    WWW_BASE,
)
from src.auth import (
    CookieExpiredError,
    _extract_cookies_from_url,
    check_cookie_needs_refresh,
    generate_correspond_path,
    generate_qr,
    get_refresh_csrf,
    poll_qr_login,
    refresh_cookie,
    _credentials_to_config_entry,
)


@pytest.mark.timeout(5)
class TestExtractCookiesFromUrl:
    def test_extracts_all_fields(self):
        url = (
            "https://passport.bilibili.com/crossDomain?"
            "SESSDATA=abc123&bili_jct=csrf456&DedeUserID=789"
        )
        result = _extract_cookies_from_url(url)
        assert result["SESSDATA"] == "abc123"
        assert result["bili_jct"] == "csrf456"
        assert result["DedeUserID"] == "789"

    def test_handles_missing_fields(self):
        url = "https://passport.bilibili.com/crossDomain?SESSDATA=abc"
        result = _extract_cookies_from_url(url)
        assert result["SESSDATA"] == "abc"
        assert "bili_jct" not in result

    def test_empty_url(self):
        result = _extract_cookies_from_url("")
        assert result == {}


@pytest.mark.timeout(10)
class TestGenerateQr:
    @pytest.mark.asyncio
    @respx.mock
    async def test_generate_qr_success(self):
        respx.get(QR_GENERATE).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "url": "https://account.bilibili.com/h5/account-h5/auth/scan-web?qrcode_key=testkey123",
                        "qrcode_key": "testkey123",
                    },
                },
            )
        )
        async with httpx.AsyncClient() as client:
            key, url = await generate_qr(client)
        assert key == "testkey123"
        assert "testkey123" in url


@pytest.mark.timeout(10)
class TestPollQrLogin:
    @pytest.mark.asyncio
    @respx.mock
    async def test_poll_success_after_retries(self):
        """Simulates: not scanned -> not scanned -> success."""
        route = respx.get(QR_POLL).mock(
            side_effect=[
                httpx.Response(200, json={"code": 0, "data": {"code": 86101}}),
                httpx.Response(200, json={"code": 0, "data": {"code": 86101}}),
                httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "code": 0,
                            "url": "https://passport.bilibili.com/crossDomain?SESSDATA=sess_val&bili_jct=csrf_val&DedeUserID=12345",
                            "refresh_token": "rt_abc",
                        },
                    },
                ),
            ]
        )
        async with httpx.AsyncClient() as client:
            result = await poll_qr_login(client, "fakekey", timeout=15, poll_interval=0.1)
        assert result["sessdata"] == "sess_val"
        assert result["bili_jct"] == "csrf_val"
        assert result["dede_user_id"] == "12345"
        assert result["refresh_token"] == "rt_abc"

    @pytest.mark.asyncio
    @respx.mock
    async def test_poll_timeout(self):
        """Mock always returns 'not scanned' - should timeout, not hang forever."""
        respx.get(QR_POLL).mock(
            return_value=httpx.Response(
                200, json={"code": 0, "data": {"code": 86101}}
            )
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(asyncio.TimeoutError):
                await poll_qr_login(client, "fakekey", timeout=1.0, poll_interval=0.1)

    @pytest.mark.asyncio
    @respx.mock
    async def test_poll_qr_expired(self):
        """QR code expired should raise RuntimeError."""
        respx.get(QR_POLL).mock(
            return_value=httpx.Response(
                200, json={"code": 0, "data": {"code": 86038}}
            )
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match="expired"):
                await poll_qr_login(client, "fakekey", timeout=5, poll_interval=0.1)


@pytest.mark.timeout(5)
class TestGenerateCorrespondPath:
    def test_returns_hex_string(self):
        result = generate_correspond_path()
        assert isinstance(result, str)
        # RSA 1024-bit key -> 128 bytes -> 256 hex chars
        assert len(result) == 256
        # Should be valid hex
        int(result, 16)

    def test_different_calls_produce_different_results(self):
        r1 = generate_correspond_path()
        r2 = generate_correspond_path()
        assert r1 != r2


@pytest.mark.timeout(10)
class TestCheckCookieNeedsRefresh:
    @pytest.mark.asyncio
    @respx.mock
    async def test_needs_refresh_true(self):
        respx.get(COOKIE_INFO).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"refresh": True, "timestamp": 1700000000000},
                },
            )
        )
        async with httpx.AsyncClient() as client:
            needs, ts = await check_cookie_needs_refresh(client, {"SESSDATA": "x"})
        assert needs is True
        assert ts == 1700000000000

    @pytest.mark.asyncio
    @respx.mock
    async def test_needs_refresh_false(self):
        respx.get(COOKIE_INFO).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"refresh": False, "timestamp": 1700000000000},
                },
            )
        )
        async with httpx.AsyncClient() as client:
            needs, ts = await check_cookie_needs_refresh(client, {"SESSDATA": "x"})
        assert needs is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_cookie_expired_on_not_logged_in(self):
        """code=-101 from /cookie/info raises CookieExpiredError."""
        respx.get(COOKIE_INFO).mock(
            return_value=httpx.Response(
                200,
                json={"code": -101, "message": "账号未登录", "data": {}},
            )
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(CookieExpiredError):
                await check_cookie_needs_refresh(client, {"SESSDATA": "dead"})


@pytest.mark.timeout(10)
class TestGetRefreshCsrf:
    @pytest.mark.asyncio
    @respx.mock
    async def test_extracts_csrf_from_html(self):
        correspond_path = "abc123hex"
        url = f"{WWW_BASE}/correspond/1/{correspond_path}"
        html_body = '<div id="1-name">refresh_csrf_token_xyz</div>'
        respx.get(url).mock(
            return_value=httpx.Response(200, text=html_body)
        )
        async with httpx.AsyncClient() as client:
            csrf = await get_refresh_csrf(client, correspond_path, {"SESSDATA": "x"})
        assert csrf == "refresh_csrf_token_xyz"

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_missing_csrf(self):
        correspond_path = "abc123hex"
        url = f"{WWW_BASE}/correspond/1/{correspond_path}"
        respx.get(url).mock(
            return_value=httpx.Response(200, text="<html>no csrf here</html>")
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match="Failed to extract"):
                await get_refresh_csrf(client, correspond_path, {"SESSDATA": "x"})


@pytest.mark.timeout(10)
class TestRefreshCookie:
    @pytest.mark.asyncio
    @respx.mock
    async def test_skip_when_no_refresh_needed(self, sample_config, tmp_path):
        respx.get(COOKIE_INFO).mock(
            return_value=httpx.Response(
                200,
                json={"code": 0, "data": {"refresh": False, "timestamp": 0}},
            )
        )
        config_path = str(tmp_path / "config.json")
        async with httpx.AsyncClient() as client:
            result = await refresh_cookie(client, "main_account", sample_config, config_path)
        # Credentials unchanged
        assert result["main_account"]["cookie"] == sample_config["main_account"]["cookie"]
        assert result["main_account"]["refresh_token"] == sample_config["main_account"]["refresh_token"]
        # Check timestamp recorded
        assert "last_cookie_check" in result["main_account"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_cookie_expired_propagates(self, sample_config, tmp_path):
        """CookieExpiredError from /cookie/info propagates through refresh_cookie."""
        respx.get(COOKIE_INFO).mock(
            return_value=httpx.Response(
                200,
                json={"code": -101, "message": "账号未登录", "data": {}},
            )
        )
        config_path = str(tmp_path / "config.json")
        async with httpx.AsyncClient() as client:
            with pytest.raises(CookieExpiredError):
                await refresh_cookie(client, "main_account", sample_config, config_path)

    @pytest.mark.asyncio
    @respx.mock
    async def test_full_refresh_flow(self, sample_config, tmp_path):
        # 1. Cookie check -> needs refresh
        respx.get(COOKIE_INFO).mock(
            return_value=httpx.Response(
                200,
                json={"code": 0, "data": {"refresh": True, "timestamp": 1700000000000}},
            )
        )

        # 2. Correspond page -> refresh_csrf
        respx.get(url__startswith=f"{WWW_BASE}/correspond/1/").mock(
            return_value=httpx.Response(
                200, text='<div id="1-name">new_refresh_csrf</div>'
            )
        )

        # 3. Cookie refresh POST -> success with Set-Cookie headers
        respx.post(COOKIE_REFRESH).mock(
            return_value=httpx.Response(
                200,
                json={"code": 0, "data": {"refresh_token": "new_rt_123"}},
                headers=[
                    ("Set-Cookie", "SESSDATA=new_sessdata; Path=/; Domain=.bilibili.com"),
                    ("Set-Cookie", "bili_jct=new_bili_jct; Path=/; Domain=.bilibili.com"),
                    ("Set-Cookie", "DedeUserID=222; Path=/; Domain=.bilibili.com"),
                ],
            )
        )

        # 4. Confirm refresh POST
        respx.post(CONFIRM_REFRESH).mock(
            return_value=httpx.Response(200, json={"code": 0})
        )

        config_path = str(tmp_path / "config.json")
        async with httpx.AsyncClient() as client:
            updated = await refresh_cookie(client, "main_account", sample_config, config_path)

        assert "new_sessdata" in updated["main_account"]["cookie"]
        assert "new_bili_jct" in updated["main_account"]["cookie"]
        assert updated["main_account"]["refresh_token"] == "new_rt_123"
        assert "last_cookie_check" in updated["main_account"]


@pytest.mark.timeout(5)
class TestCredentialsToConfigEntry:
    def test_converts_correctly(self):
        creds = {
            "sessdata": "s1",
            "bili_jct": "j1",
            "dede_user_id": "u1",
            "refresh_token": "rt1",
        }
        entry = _credentials_to_config_entry(creds)
        assert "SESSDATA=s1" in entry["cookie"]
        assert "bili_jct=j1" in entry["cookie"]
        assert "DedeUserID=u1" in entry["cookie"]
        assert entry["refresh_token"] == "rt1"
