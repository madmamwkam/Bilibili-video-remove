"""Tests for transfer engine - the core pipeline."""

import httpx
import pytest
import respx

from src.anti_ban import CircuitBreaker, RateLimiter
from src.api_endpoints import FAV_RESOURCE_DEAL, FAV_RESOURCE_LIST
from src.transfer import (
    add_to_favorites,
    fetch_favorites_page,
    remove_from_favorites,
    transfer_all,
)


def _make_media(aid: int, bvid: str, title: str) -> dict:
    """Helper to create a mock media item as returned by the API."""
    return {"id": aid, "bvid": bvid, "title": title, "type": 2}


def _zero_limiter() -> RateLimiter:
    """Rate limiter with zero delays for testing."""
    return RateLimiter(read_delay_range=(0, 0), write_delay_range=(0, 0))


@pytest.mark.timeout(10)
class TestFetchFavoritesPage:
    @pytest.mark.asyncio
    @respx.mock
    async def test_fetches_page_successfully(self):
        respx.get(FAV_RESOURCE_LIST).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "medias": [
                            _make_media(1001, "BV1test1", "Video One"),
                            _make_media(1002, "BV1test2", "Video Two"),
                        ],
                        "has_more": True,
                    },
                },
            )
        )
        async with httpx.AsyncClient() as client:
            items, has_more = await fetch_favorites_page(
                client, "12345", 1, {"SESSDATA": "x"}
            )
        assert len(items) == 2
        assert items[0]["id"] == 1001
        assert items[0]["bvid"] == "BV1test1"
        assert items[1]["title"] == "Video Two"
        assert has_more is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_last_page_has_more_false(self):
        respx.get(FAV_RESOURCE_LIST).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "medias": [_make_media(2001, "BV2test1", "Last Video")],
                        "has_more": False,
                    },
                },
            )
        )
        async with httpx.AsyncClient() as client:
            items, has_more = await fetch_favorites_page(
                client, "12345", 2, {"SESSDATA": "x"}
            )
        assert len(items) == 1
        assert has_more is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_page(self):
        respx.get(FAV_RESOURCE_LIST).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"medias": None, "has_more": False},
                },
            )
        )
        async with httpx.AsyncClient() as client:
            items, has_more = await fetch_favorites_page(
                client, "12345", 1, {"SESSDATA": "x"}
            )
        assert items == []
        assert has_more is False


@pytest.mark.timeout(10)
class TestAddToFavorites:
    @pytest.mark.asyncio
    @respx.mock
    async def test_add_success(self):
        respx.post(FAV_RESOURCE_DEAL).mock(
            return_value=httpx.Response(
                200, json={"code": 0, "message": "0"}
            )
        )
        async with httpx.AsyncClient() as client:
            result = await add_to_favorites(
                client, 1001, "67890", {"SESSDATA": "x"}, "csrf_val"
            )
        assert result == "added"

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_already_exists_returns_skipped(self):
        respx.post(FAV_RESOURCE_DEAL).mock(
            return_value=httpx.Response(
                200, json={"code": 11007, "message": "已存在"}
            )
        )
        async with httpx.AsyncClient() as client:
            result = await add_to_favorites(
                client, 1001, "67890", {"SESSDATA": "x"}, "csrf_val"
            )
        assert result == "skipped"

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_unknown_error(self):
        respx.post(FAV_RESOURCE_DEAL).mock(
            return_value=httpx.Response(
                200, json={"code": -999, "message": "unknown error"}
            )
        )
        async with httpx.AsyncClient() as client:
            result = await add_to_favorites(
                client, 1001, "67890", {"SESSDATA": "x"}, "csrf_val"
            )
        assert isinstance(result, tuple)
        assert result[0] == "error"

    @pytest.mark.asyncio
    @respx.mock
    async def test_add_risk_control_412(self):
        respx.post(FAV_RESOURCE_DEAL).mock(
            return_value=httpx.Response(
                200, json={"code": -412, "message": "请求被拦截"}
            )
        )
        async with httpx.AsyncClient() as client:
            result = await add_to_favorites(
                client, 1001, "67890", {"SESSDATA": "x"}, "csrf_val"
            )
        assert isinstance(result, tuple)
        assert result[2] == -412


@pytest.mark.timeout(10)
class TestRemoveFromFavorites:
    @pytest.mark.asyncio
    @respx.mock
    async def test_remove_success(self):
        respx.post(FAV_RESOURCE_DEAL).mock(
            return_value=httpx.Response(200, json={"code": 0, "message": "0"})
        )
        async with httpx.AsyncClient() as client:
            result = await remove_from_favorites(
                client, 1001, "12345", {"SESSDATA": "x"}, "csrf_val"
            )
        assert result is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_remove_failure(self):
        respx.post(FAV_RESOURCE_DEAL).mock(
            return_value=httpx.Response(200, json={"code": -999, "message": "fail"})
        )
        async with httpx.AsyncClient() as client:
            result = await remove_from_favorites(
                client, 1001, "12345", {"SESSDATA": "x"}, "csrf_val"
            )
        assert result is False


@pytest.mark.timeout(15)
class TestTransferAll:
    @pytest.mark.asyncio
    @respx.mock
    async def test_full_pipeline_two_pages(self, sample_config):
        """Two pages of videos, mix of success and already-exists, all deleted."""
        # Page 1: 2 videos, has_more=True
        respx.get(FAV_RESOURCE_LIST, params__contains={"pn": "1"}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "medias": [
                            _make_media(1001, "BV1a", "Video A"),
                            _make_media(1002, "BV1b", "Video B"),
                        ],
                        "has_more": True,
                    },
                },
            )
        )
        # Page 2: 1 video, has_more=False
        respx.get(FAV_RESOURCE_LIST, params__contains={"pn": "2"}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "medias": [_make_media(1003, "BV1c", "Video C")],
                        "has_more": False,
                    },
                },
            )
        )

        # Deal endpoint handles both add and delete POSTs:
        # Video A: add(success) -> delete(success)
        # Video B: add(already exists) -> delete(success)
        # Video C: add(success) -> delete(success)
        deal_responses = [
            httpx.Response(200, json={"code": 0, "message": "0"}),       # add A
            httpx.Response(200, json={"code": 0, "message": "0"}),       # delete A
            httpx.Response(200, json={"code": 11007, "message": "已存在"}),  # add B (skip)
            httpx.Response(200, json={"code": 0, "message": "0"}),       # delete B
            httpx.Response(200, json={"code": 0, "message": "0"}),       # add C
            httpx.Response(200, json={"code": 0, "message": "0"}),       # delete C
        ]
        respx.post(FAV_RESOURCE_DEAL).mock(side_effect=deal_responses)

        async with httpx.AsyncClient() as client:
            tally = await transfer_all(
                client,
                sample_config,
                _zero_limiter(),
                CircuitBreaker(),
            )

        assert tally["added"] == 2
        assert tally["skipped"] == 1
        assert tally["deleted"] == 3
        assert tally["error"] == 0
        assert tally["total"] == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_single_page_all_skipped_then_deleted(self, sample_config):
        """All videos already exist — skipped but still deleted from source."""
        respx.get(FAV_RESOURCE_LIST).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "medias": [
                            _make_media(2001, "BV2a", "Old A"),
                            _make_media(2002, "BV2b", "Old B"),
                        ],
                        "has_more": False,
                    },
                },
            )
        )
        # add(skip) -> delete(ok) -> add(skip) -> delete(ok)
        respx.post(FAV_RESOURCE_DEAL).mock(
            side_effect=[
                httpx.Response(200, json={"code": 11007, "message": "已存在"}),
                httpx.Response(200, json={"code": 0, "message": "0"}),
                httpx.Response(200, json={"code": 11007, "message": "已存在"}),
                httpx.Response(200, json={"code": 0, "message": "0"}),
            ]
        )

        async with httpx.AsyncClient() as client:
            tally = await transfer_all(
                client,
                sample_config,
                _zero_limiter(),
                CircuitBreaker(),
            )

        assert tally["added"] == 0
        assert tally["skipped"] == 2
        assert tally["deleted"] == 2
        assert tally["total"] == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_source_folder(self, sample_config):
        """Empty source folder — nothing to transfer."""
        respx.get(FAV_RESOURCE_LIST).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"medias": None, "has_more": False},
                },
            )
        )

        async with httpx.AsyncClient() as client:
            tally = await transfer_all(
                client,
                sample_config,
                _zero_limiter(),
                CircuitBreaker(),
            )

        assert tally["total"] == 0
        assert tally["deleted"] == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_no_delete_on_failure(self, sample_config):
        """On add error, video is NOT deleted from source."""
        respx.get(FAV_RESOURCE_LIST).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "medias": [
                            _make_media(3001, "BV3a", "A"),
                            _make_media(3002, "BV3b", "B"),
                        ],
                        "has_more": False,
                    },
                },
            )
        )
        # add(success) -> delete(ok) -> add(error, no delete follows)
        respx.post(FAV_RESOURCE_DEAL).mock(
            side_effect=[
                httpx.Response(200, json={"code": 0, "message": "0"}),      # add A ok
                httpx.Response(200, json={"code": 0, "message": "0"}),      # delete A ok
                httpx.Response(200, json={"code": -999, "message": "error"}),  # add B fail
                # No delete call for B
            ]
        )

        async with httpx.AsyncClient() as client:
            tally = await transfer_all(
                client,
                sample_config,
                _zero_limiter(),
                CircuitBreaker(),
            )

        assert tally["added"] == 1
        assert tally["error"] == 1
        assert tally["deleted"] == 1
        assert tally["total"] == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_delete_failure_counted(self, sample_config):
        """Delete failure doesn't crash — just not counted."""
        respx.get(FAV_RESOURCE_LIST).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "medias": [_make_media(4001, "BV4a", "X")],
                        "has_more": False,
                    },
                },
            )
        )
        # add(success) -> delete(fail)
        respx.post(FAV_RESOURCE_DEAL).mock(
            side_effect=[
                httpx.Response(200, json={"code": 0, "message": "0"}),       # add ok
                httpx.Response(200, json={"code": -999, "message": "fail"}),  # delete fail
            ]
        )

        async with httpx.AsyncClient() as client:
            tally = await transfer_all(
                client,
                sample_config,
                _zero_limiter(),
                CircuitBreaker(),
            )

        assert tally["added"] == 1
        assert tally["deleted"] == 0  # Delete failed
        assert tally["total"] == 1
