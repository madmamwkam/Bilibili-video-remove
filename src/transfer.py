"""Direct one-way transfer engine for Bilibili favorites.

NO deduplication logic. Pure pipeline: pull from source -> push to target.
"Already exists" errors are caught from the API response and logged as skips.
Successfully transferred videos are deleted from the source folder.
"""

import httpx
from loguru import logger

from src.anti_ban import CircuitBreaker, RateLimiter
from src.api_endpoints import (
    CODE_ACCESS_DENIED,
    CODE_ALREADY_EXISTS,
    CODE_NOT_LOGGED_IN,
    CODE_SUCCESS,
    FAV_RESOURCE_DEAL,
    FAV_RESOURCE_LIST,
    api_get,
    api_post,
)
from src.config import get_cookie_dict, get_csrf


class SessionExpiredError(Exception):
    """Raised when a -101 (not logged in) response is detected during transfer."""


async def fetch_favorites_page(
    client: httpx.AsyncClient,
    media_id: str,
    page: int,
    cookies: dict,
) -> tuple[list[dict], bool]:
    """Fetch one page of favorites from a folder.

    Returns:
        (items, has_more). Returns ([], False) on API error (logged as warning).

    Raises:
        SessionExpiredError: If -101 (not logged in) is returned.
    """
    result = await api_get(
        client,
        FAV_RESOURCE_LIST,
        params={"media_id": media_id, "pn": page, "ps": 20},
        cookies=cookies,
    )

    api_code = result.get("code", 0)

    if api_code == CODE_NOT_LOGGED_IN:
        raise SessionExpiredError(
            f"Sub-account session expired while reading favorites (page {page})"
        )

    if api_code != CODE_SUCCESS:
        logger.warning(
            "fetch_favorites_page error: code={}, status={}, msg={}",
            api_code, result.get("status_code"), result.get("message", ""),
        )
        return [], False, api_code

    data = result.get("data") or {}
    medias = data.get("medias", []) or []
    has_more = data.get("has_more", False)

    items = []
    for media in medias:
        aid = media.get("id")
        if aid is None:
            logger.warning("Skipping media entry with missing id: {}", media)
            continue
        items.append({
            "id": aid,
            "bvid": media.get("bvid", ""),
            "title": media.get("title", ""),
        })

    logger.info(
        "Fetched page {} of folder {}: {} items, has_more={}",
        page, media_id, len(items), has_more,
    )
    return items, has_more, CODE_SUCCESS

async def add_to_favorites(
    client: httpx.AsyncClient,
    aid: int,
    target_media_id: str,
    cookies: dict,
    csrf: str,
) -> str:
    """Add a single video to the target favorites folder.

    Args:
        client: HTTP client
        aid: Video's numeric aid (used as rid parameter)
        target_media_id: Target favorites folder ID
        cookies: Main-account cookies dict
        csrf: Main-account bili_jct value

    Returns:
        "added" on success, "skipped" if already exists, "error" otherwise
    """
    result = await api_post(
        client,
        FAV_RESOURCE_DEAL,
        data={
            "rid": aid,
            "type": 2,
            "add_media_ids": target_media_id,
            "csrf": csrf,
        },
        cookies=cookies,
    )

    status_code = result.get("status_code", 200)
    api_code = result.get("code")

    if api_code == CODE_SUCCESS:
        logger.info("Added video aid={} to folder {}", aid, target_media_id)
        return "added"
    elif api_code == CODE_ALREADY_EXISTS:
        logger.info("Video aid={} already exists in folder {}, skipped", aid, target_media_id)
        return "skipped"
    else:
        logger.warning(
            "Failed to add video aid={}: code={}, msg={}",
            aid, api_code, result.get("message", ""),
        )
        return "error", status_code, api_code


async def remove_from_favorites(
    client: httpx.AsyncClient,
    aid: int,
    source_media_id: str,
    cookies: dict,
    csrf: str,
) -> bool:
    """Remove a single video from the source favorites folder.

    Args:
        client: HTTP client
        aid: Video's numeric aid
        source_media_id: Source favorites folder ID
        cookies: Sub-account cookies dict
        csrf: Sub-account bili_jct value

    Returns:
        True if removed successfully, False otherwise
    """
    result = await api_post(
        client,
        FAV_RESOURCE_DEAL,
        data={
            "rid": aid,
            "type": 2,
            "del_media_ids": source_media_id,
            "csrf": csrf,
        },
        cookies=cookies,
    )

    api_code = result.get("code")
    if api_code == CODE_SUCCESS:
        logger.info("Removed video aid={} from source folder {}", aid, source_media_id)
        return True
    else:
        logger.warning(
            "Failed to remove video aid={} from source: code={}, msg={}",
            aid, api_code, result.get("message", ""),
        )
        return False


async def transfer_all(
    client: httpx.AsyncClient,
    config: dict,
    rate_limiter: RateLimiter,
    circuit_breaker: CircuitBreaker,
) -> dict:
    """Execute the full one-way transfer pipeline.

    Pulls all videos from sub-account source folder, page by page,
    and pushes each directly to the main-account target folder.

    NO deduplication. API "already exists" errors are caught and logged.

    Args:
        client: HTTP client
        config: Full config dict
        rate_limiter: Rate limiter for delays
        circuit_breaker: Circuit breaker for risk control

    Returns:
        Tally dict {"added": N, "skipped": N, "error": N, "deleted": N, "total": N}
    """
    sub_cookies = get_cookie_dict("sub_account", config)
    main_cookies = get_cookie_dict("main_account", config)
    csrf_main = get_csrf("main_account", config)
    csrf_sub = get_csrf("sub_account", config)
    source_media_id = config["sub_account"]["source_media_id"]
    target_media_id = config["main_account"]["target_media_id"]

    tally = {"added": 0, "skipped": 0, "error": 0, "deleted": 0, "total": 0}

    logger.info(
        "Starting transfer: source={} -> target={}",
        source_media_id, target_media_id,
    )

    # Phase 1: Collect all items before making any changes.
    # This prevents pagination drift — if we deleted items while paginating,
    # remaining items would shift up and page N+1 would skip what was on page N.
    all_items: list[dict] = []
    page = 1
    while True:
        if circuit_breaker.is_tripped:
            await circuit_breaker.wait_if_tripped()

        items, has_more, page_code = await fetch_favorites_page(
            client, source_media_id, page, sub_cookies,
        )
        if page_code != CODE_SUCCESS:
            circuit_breaker.check_response(200, page_code)
            if circuit_breaker.is_tripped:
                await circuit_breaker.wait_if_tripped()
            break
        all_items.extend(items)

        if not has_more:
            break

        await rate_limiter.read_delay()
        page += 1

    logger.info("Collected {} items from source folder", len(all_items))

    # Phase 2: Process collected items — add to target, then delete from source.
    for item in all_items:
        # Check circuit breaker before each write
        if circuit_breaker.is_tripped:
            await circuit_breaker.wait_if_tripped()

        result = await add_to_favorites(
            client, item["id"], target_media_id, main_cookies, csrf_main,
        )

        # Handle tuple return for error cases
        if isinstance(result, tuple):
            status_str, status_code, api_code = result
            if api_code == CODE_NOT_LOGGED_IN:
                raise SessionExpiredError(
                    "Main account session expired during transfer"
                )
            circuit_breaker.check_response(status_code, api_code)
            tally["error"] += 1
        else:
            tally[result] += 1

            # Delete from source after successful add or skip (already exists)
            if result in ("added", "skipped"):
                await rate_limiter.write_delay()
                deleted = await remove_from_favorites(
                    client, item["id"], source_media_id, sub_cookies, csrf_sub,
                )
                if deleted:
                    tally["deleted"] += 1

        tally["total"] += 1

        # Mandatory write delay (NO concurrent writes)
        await rate_limiter.write_delay()

    logger.info(
        "Transfer complete: {} added, {} skipped, {} deleted, {} errors (total: {})",
        tally["added"], tally["skipped"], tally["deleted"], tally["error"], tally["total"],
    )
    return tally
