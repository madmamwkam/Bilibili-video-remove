"""Anti-ban system: rate limiting and circuit breaker for Bilibili API."""

import asyncio
import random

from loguru import logger

from src.api_endpoints import CODE_ACCESS_DENIED, CODE_RISK_CONTROL


class RateLimiter:
    """Enforces mandatory delays between API calls to avoid bans.

    Read operations: 3-5 second random delay between pages.
    Write operations: 10-20 second random delay between POSTs.

    Delay ranges are configurable for testing (pass (0,0) to skip waits).
    """

    def __init__(
        self,
        read_delay_range: tuple[float, float] = (3.0, 5.0),
        write_delay_range: tuple[float, float] = (10.0, 20.0),
    ):
        self._read_range = read_delay_range
        self._write_range = write_delay_range

    async def read_delay(self) -> float:
        """Sleep for a random duration in the read range.

        Returns:
            Actual delay in seconds.
        """
        delay = random.uniform(*self._read_range)
        if delay > 0:
            logger.debug("Read delay: {:.1f}s", delay)
            await asyncio.sleep(delay)
        return delay

    async def write_delay(self) -> float:
        """Sleep for a random duration in the write range.

        Returns:
            Actual delay in seconds.
        """
        delay = random.uniform(*self._write_range)
        if delay > 0:
            logger.debug("Write delay: {:.1f}s", delay)
            await asyncio.sleep(delay)
        return delay


class CircuitBreaker:
    """Suspends all operations when risk control is detected.

    Triggers on HTTP 403 or API code -412.
    When tripped, forces a configurable suspension period (default 4 hours).
    """

    def __init__(self, suspend_hours: float = 4.0):
        self._suspend_seconds = suspend_hours * 3600
        self._tripped = False

    @property
    def is_tripped(self) -> bool:
        return self._tripped

    def check_response(self, status_code: int, api_code: int | None = None) -> bool:
        """Check if the response triggers the circuit breaker.

        Args:
            status_code: HTTP status code
            api_code: Bilibili API business code (from JSON response)

        Returns:
            True if circuit breaker was tripped by this response.
        """
        if status_code == 403 or api_code in (CODE_RISK_CONTROL, CODE_ACCESS_DENIED):
            self._tripped = True
            logger.error(
                "Circuit breaker TRIPPED! status={}, api_code={}",
                status_code,
                api_code,
            )
            return True
        return False

    async def wait_if_tripped(self) -> bool:
        """If tripped, suspend for the configured duration.

        Returns:
            True if suspension occurred, False if not tripped.
        """
        if not self._tripped:
            return False

        hours = self._suspend_seconds / 3600
        logger.error(
            "Circuit breaker active — suspending ALL tasks for {:.1f} hours...",
            hours,
        )
        await asyncio.sleep(self._suspend_seconds)
        self._tripped = False
        logger.info("Circuit breaker reset, resuming operations")
        return True

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._tripped = False
