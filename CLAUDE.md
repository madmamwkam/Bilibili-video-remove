# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

B站(Bilibili)收藏夹跨端迁移工具 — a Python async daemon that transfers favorites from a sub-account to a main account on Bilibili. Pure pipeline architecture: pull source -> push target, no deduplication logic allowed.

## Critical Constraints

- **NO local database, NO deduplication** — the API itself handles "already exists" (code 11007), which is caught and logged
- All Bilibili API specs follow [BACNext](https://bacnext.apifox.cn) documentation
- Must use the existing `bilibili-videos-remove/` virtual environment — no global pip installs
- `rid` parameter in `/x/v3/fav/resource/deal` must use numeric `aid` (from `media["id"]`), NOT `bvid`

## Tech Stack

Python 3.13.2, httpx (async HTTP), APScheduler 3.x (AsyncIOScheduler), qrcode, loguru, cryptography (RSA for cookie refresh)

## Project Structure

```
src/
  config.py          # load/save config.json, cookie string parsing
  api_endpoints.py   # URL constants, DEFAULT_HEADERS, api_get/api_post helpers
  auth.py            # QR login (generate/poll), cookie refresh (RSA correspondPath)
  transfer.py        # fetch_favorites_page, add_to_favorites, remove_from_favorites, transfer_all pipeline
  anti_ban.py        # RateLimiter (configurable delays), CircuitBreaker (403/-412)
  main.py            # CLI menu, APScheduler setup, run_transfer_job orchestrator
tests/
  conftest.py        # sample_config and empty_config fixtures
  test_config.py     # Config load/save/parse tests
  test_auth.py       # Auth with respx-mocked HTTP, timeout tests
  test_transfer.py   # Pipeline tests with mock pages and deal responses
  test_anti_ban.py   # Rate limiter ranges, circuit breaker with monkeypatched sleep
  test_integration.py # Full pipeline + scheduler registration
```

## Commands

```bash
# Activate venv
bilibili-videos-remove\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the tool
python -m src.main

# Run all tests (61 tests, ~47s)
python -m pytest tests/ -v --timeout=30

# Run single test file
python -m pytest tests/test_transfer.py -v --timeout=15

# Run a specific test
python -m pytest tests/test_auth.py::TestPollQrLogin::test_poll_timeout -v
```

## Testing Patterns

- Every test has `@pytest.mark.timeout(N)` (5-15s) — tests never block indefinitely
- All HTTP is mocked with `respx` — zero real network calls
- `RateLimiter` accepts configurable delay ranges — tests pass `(0, 0)` for instant completion
- `CircuitBreaker.wait_if_tripped()` is tested via `monkeypatch` on `asyncio.sleep`
- QR poll uses `asyncio.wait_for(timeout=N)` internally — timeout tests verify it raises `TimeoutError`

## Key API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `passport.bilibili.com/x/passport-login/web/qrcode/generate` | GET | Generate QR code |
| `passport.bilibili.com/x/passport-login/web/qrcode/poll` | GET | Poll login status |
| `passport.bilibili.com/x/passport-login/web/cookie/info` | GET | Check cookie freshness |
| `passport.bilibili.com/x/passport-login/web/cookie/refresh` | POST | Refresh cookies |
| `api.bilibili.com/x/v3/fav/resource/list` | GET | List favorites (pn/ps pagination) |
| `api.bilibili.com/x/v3/fav/resource/deal` | POST | Add or remove video from favorites folder |

## Error Code Handling

- `0` → success (added)
- `11007` → already exists (skip, INFO log)
- `-412` → risk control (circuit breaker: suspend 4h)
- `-101` → not logged in (attempt cookie refresh)
- `HTTP 403` → circuit breaker trigger
