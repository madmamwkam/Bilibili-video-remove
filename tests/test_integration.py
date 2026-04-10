"""Integration tests - full pipeline with all mocks."""

import asyncio

import httpx
import pytest
import respx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.api_endpoints import (
    COOKIE_INFO,
    FAV_RESOURCE_DEAL,
    FAV_RESOURCE_LIST,
)
from src.config import save_config
from src.main import run_transfer_job, start_scheduler


def _make_media(aid: int, bvid: str, title: str) -> dict:
    return {"id": aid, "bvid": bvid, "title": title, "type": 2}


@pytest.mark.timeout(15)
class TestRunTransferJob:
    @pytest.mark.asyncio
    @respx.mock
    async def test_full_transfer_cycle(self, sample_config, tmp_path, monkeypatch):
        """Test a complete transfer job with mocked HTTP and zero delays."""
        # Save config to disk
        config_path = str(tmp_path / "config.json")
        save_config(sample_config, config_path)

        # Mock cookie check -> no refresh needed (for both accounts)
        respx.get(COOKIE_INFO).mock(
            return_value=httpx.Response(
                200,
                json={"code": 0, "data": {"refresh": False, "timestamp": 0}},
            )
        )

        # Mock favorites list - 1 page with 2 items
        respx.get(FAV_RESOURCE_LIST).mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "medias": [
                            _make_media(5001, "BV5a", "Integration A"),
                            _make_media(5002, "BV5b", "Integration B"),
                        ],
                        "has_more": False,
                    },
                },
            )
        )

        # Mock favorites deal - add+delete sequence:
        # add A(success) -> delete A(ok) -> add B(already exists) -> delete B(ok)
        respx.post(FAV_RESOURCE_DEAL).mock(
            side_effect=[
                httpx.Response(200, json={"code": 0, "message": "0"}),
                httpx.Response(200, json={"code": 0, "message": "0"}),
                httpx.Response(200, json={"code": 11007, "message": "已存在"}),
                httpx.Response(200, json={"code": 0, "message": "0"}),
            ]
        )

        # Patch RateLimiter to use zero delays
        from src import anti_ban

        original_init = anti_ban.RateLimiter.__init__

        def zero_init(self, **kwargs):
            original_init(
                self,
                read_delay_range=(0, 0),
                write_delay_range=(0, 0),
            )

        monkeypatch.setattr(anti_ban.RateLimiter, "__init__", zero_init)

        tally = await run_transfer_job(config_path)

        assert tally["added"] == 1
        assert tally["skipped"] == 1
        assert tally["deleted"] == 2
        assert tally["error"] == 0
        assert tally["total"] == 2

    @pytest.mark.asyncio
    async def test_unconfigured_accounts_returns_empty(self, tmp_path):
        """When accounts aren't configured, should return empty tally."""
        config_path = str(tmp_path / "config.json")
        # No config file exists -> defaults with empty cookies
        tally = await run_transfer_job(config_path)
        assert tally["total"] == 0


@pytest.mark.timeout(5)
class TestScheduler:
    def test_scheduler_registers_job(self):
        """Verify APScheduler job registration without running it."""
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            run_transfer_job,
            trigger=IntervalTrigger(hours=24),
            id="transfer_job",
            replace_existing=True,
            max_instances=1,
        )
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "transfer_job"

    def test_scheduler_custom_interval(self):
        """Different interval should be reflected in the job."""
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            run_transfer_job,
            trigger=IntervalTrigger(hours=6),
            id="transfer_job",
            replace_existing=True,
            max_instances=1,
        )
        job = scheduler.get_jobs()[0]
        assert job.trigger.interval.total_seconds() == 6 * 3600
