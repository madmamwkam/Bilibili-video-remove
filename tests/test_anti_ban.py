"""Tests for anti-ban module - rate limiter and circuit breaker."""

import asyncio

import pytest

from src.anti_ban import CircuitBreaker, RateLimiter
from src.api_endpoints import CODE_RISK_CONTROL


@pytest.mark.timeout(5)
class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_read_delay_respects_range(self):
        limiter = RateLimiter(read_delay_range=(0.01, 0.02), write_delay_range=(0.01, 0.02))
        delay = await limiter.read_delay()
        assert 0.01 <= delay <= 0.02

    @pytest.mark.asyncio
    async def test_write_delay_respects_range(self):
        limiter = RateLimiter(read_delay_range=(0.01, 0.02), write_delay_range=(0.03, 0.04))
        delay = await limiter.write_delay()
        assert 0.03 <= delay <= 0.04

    @pytest.mark.asyncio
    async def test_zero_delay_returns_instantly(self):
        limiter = RateLimiter(read_delay_range=(0, 0), write_delay_range=(0, 0))
        delay = await limiter.read_delay()
        assert delay == 0
        delay = await limiter.write_delay()
        assert delay == 0

    @pytest.mark.asyncio
    async def test_default_ranges(self):
        limiter = RateLimiter()
        assert limiter._read_range == (3.0, 5.0)
        assert limiter._write_range == (10.0, 20.0)


@pytest.mark.timeout(5)
class TestCircuitBreaker:
    def test_not_tripped_initially(self):
        cb = CircuitBreaker()
        assert cb.is_tripped is False

    def test_trips_on_403(self):
        cb = CircuitBreaker()
        assert cb.check_response(403, None) is True
        assert cb.is_tripped is True

    def test_trips_on_minus_412(self):
        cb = CircuitBreaker()
        assert cb.check_response(200, CODE_RISK_CONTROL) is True
        assert cb.is_tripped is True

    def test_no_trip_on_normal_response(self):
        cb = CircuitBreaker()
        assert cb.check_response(200, 0) is False
        assert cb.is_tripped is False

    def test_no_trip_on_other_error_codes(self):
        cb = CircuitBreaker()
        assert cb.check_response(200, -101) is False
        assert cb.check_response(200, 11007) is False
        assert cb.is_tripped is False

    def test_reset(self):
        cb = CircuitBreaker()
        cb.check_response(403, None)
        assert cb.is_tripped is True
        cb.reset()
        assert cb.is_tripped is False

    @pytest.mark.asyncio
    async def test_wait_if_tripped_does_nothing_when_not_tripped(self):
        cb = CircuitBreaker()
        result = await cb.wait_if_tripped()
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_if_tripped_sleeps_correct_duration(self, monkeypatch):
        """Mock asyncio.sleep to verify duration without actually waiting."""
        sleep_calls = []

        async def mock_sleep(duration):
            sleep_calls.append(duration)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        cb = CircuitBreaker(suspend_hours=4.0)
        cb.check_response(403, None)
        result = await cb.wait_if_tripped()

        assert result is True
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 14400.0  # 4 hours in seconds
        assert cb.is_tripped is False  # Reset after wait

    @pytest.mark.asyncio
    async def test_wait_if_tripped_on_412(self, monkeypatch):
        sleep_calls = []

        async def mock_sleep(duration):
            sleep_calls.append(duration)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        cb = CircuitBreaker(suspend_hours=5.0)
        cb.check_response(200, -412)
        await cb.wait_if_tripped()

        assert sleep_calls[0] == 18000.0  # 5 hours

    @pytest.mark.asyncio
    async def test_custom_suspend_hours(self, monkeypatch):
        sleep_calls = []

        async def mock_sleep(duration):
            sleep_calls.append(duration)

        monkeypatch.setattr(asyncio, "sleep", mock_sleep)

        cb = CircuitBreaker(suspend_hours=0.5)
        cb.check_response(403, None)
        await cb.wait_if_tripped()

        assert sleep_calls[0] == 1800.0  # 0.5 hours
