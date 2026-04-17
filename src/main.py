"""Main entry point: CLI argument dispatch, scheduler, and orchestration."""

import argparse
import asyncio
import sys
import warnings
from datetime import datetime, timedelta, timezone

# Suppress tzlocal timezone-mismatch warning from APScheduler on Termux/proot-distro.
# Our code uses timezone.utc explicitly everywhere, so local timezone misconfiguration
# is harmless but generates noise on every startup.
warnings.filterwarnings("ignore", message="Timezone offset does not match system offset")

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.anti_ban import CircuitBreaker, RateLimiter
from src.api_endpoints import DEFAULT_HEADERS
from src.auth import CookieExpiredError, login_account, refresh_cookie, _credentials_to_config_entry
from src.config import load_config, save_config, get_cookie_dict
from src.transfer import SessionExpiredError, transfer_all

# Cookie check is rate-limited: at most once per 12 hours per account
COOKIE_CHECK_MIN_INTERVAL_HOURS = 12

# Configure loguru: console + rotating file
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def setup_logging() -> None:
    """Configure loguru with console and file output."""
    logger.remove()
    logger.add(
        sys.stderr,
        format=LOG_FORMAT,
        level="INFO",
        colorize=True,
    )
    logger.add(
        "logs/transfer_{time:YYYY-MM-DD}.log",
        format=LOG_FORMAT,
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


async def _check_and_refresh_cookie(
    client: httpx.AsyncClient,
    account_key: str,
    config: dict,
    config_path: str,
) -> dict:
    """Check cookie status and refresh if needed, respecting the 12h rate limit.

    Returns updated config. Raises CookieExpiredError if cookie is dead.
    """
    last_check_str = config.get(account_key, {}).get("last_cookie_check")
    if last_check_str:
        try:
            last_check = datetime.fromisoformat(last_check_str)
            if last_check.tzinfo is None:
                last_check = last_check.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - last_check
            if elapsed < timedelta(hours=COOKIE_CHECK_MIN_INTERVAL_HOURS):
                logger.debug(
                    "【{}】Cookie check skipped ({}h < {}h minimum interval)",
                    account_key,
                    round(elapsed.total_seconds() / 3600, 1),
                    COOKIE_CHECK_MIN_INTERVAL_HOURS,
                )
                return config
        except ValueError:
            pass  # Malformed timestamp — proceed with check

    return await refresh_cookie(client, account_key, config, config_path)


async def run_transfer_job(config_path: str = "config.json") -> dict:
    """Execute one complete transfer cycle.

    1. Load config
    2. Check and refresh cookies (rate-limited to 1x/12h per account)
    3. Run transfer pipeline
    4. Log summary

    Returns:
        Transfer tally dict
    """
    config = load_config(config_path)

    # Validate config
    if not config["sub_account"].get("cookie"):
        logger.error("Sub account not configured. Run setup first.")
        return {"added": 0, "skipped": 0, "error": 0, "total": 0}
    if not config["main_account"].get("cookie"):
        logger.error("Main account not configured. Run setup first.")
        return {"added": 0, "skipped": 0, "error": 0, "total": 0}

    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        # Check and refresh cookies (rate-limited, CookieExpiredError aborts the job)
        for account_key in ("sub_account", "main_account"):
            try:
                config = await _check_and_refresh_cookie(
                    client, account_key, config, config_path
                )
            except CookieExpiredError as e:
                logger.error(
                    "【{}】Cookie expired: {}. Re-run 'setup' to re-login.",
                    account_key, e,
                )
                return {"added": 0, "skipped": 0, "error": 0, "deleted": 0, "total": 0}
            except Exception as e:
                logger.warning("Cookie check failed for {}: {}", account_key, e)

        # Create rate limiter and circuit breaker
        ab = config.get("anti_ban", {})
        rate_limiter = RateLimiter(
            read_delay_range=(ab.get("read_delay_min", 3.0), ab.get("read_delay_max", 5.0)),
            write_delay_range=(ab.get("write_delay_min", 10.0), ab.get("write_delay_max", 20.0)),
        )
        circuit_breaker = CircuitBreaker(suspend_hours=4.0)

        # Run transfer
        try:
            tally = await transfer_all(client, config, rate_limiter, circuit_breaker)
        except SessionExpiredError as e:
            logger.error(
                "Session expired during transfer: {}. Re-run 'setup' to re-login.", e
            )
            # Clear last_cookie_check for both accounts so next run re-checks immediately
            for key in ("sub_account", "main_account"):
                if "last_cookie_check" in config.get(key, {}):
                    config[key] = {k: v for k, v in config[key].items() if k != "last_cookie_check"}
            save_config(config, config_path)
            return {"added": 0, "skipped": 0, "error": 0, "deleted": 0, "total": 0}
        except Exception as e:
            logger.error("Unexpected error during transfer: {}", e, exc_info=True)
            return {"added": 0, "skipped": 0, "error": 0, "deleted": 0, "total": 0}

    logger.info(
        "=== Transfer job complete: +{} added, ~{} skipped, !{} errors ===",
        tally["added"], tally["skipped"], tally["error"],
    )
    return tally


async def interactive_setup(config_path: str = "config.json") -> dict:
    """Interactive first-time setup: login both accounts and configure folders.

    Returns:
        The saved config dict
    """
    config = load_config(config_path)

    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        # Login sub account
        print("\n[1/4] 登录副账号（视频源）")
        sub_creds = await login_account(client, "副账号", timeout=180)
        sub_entry = _credentials_to_config_entry(sub_creds)

        # Login main account
        print("\n[2/4] 登录主账号（视频目标）")
        main_creds = await login_account(client, "主账号", timeout=180)
        main_entry = _credentials_to_config_entry(main_creds)

    # Get folder IDs
    print("\n[3/4] 配置收藏夹ID")
    source_id = input("请输入副账号源收藏夹ID (source_media_id): ").strip()
    target_id = input("请输入主账号目标收藏夹ID (target_media_id): ").strip()

    # Build config
    config = {
        "sub_account": {
            **sub_entry,
            "source_media_id": source_id,
        },
        "main_account": {
            **main_entry,
            "target_media_id": target_id,
        },
        "task_schedule": config.get("task_schedule", {"interval_hours": 24}),
        "anti_ban": config.get("anti_ban", {
            "read_delay_min": 3.0,
            "read_delay_max": 5.0,
            "write_delay_min": 10.0,
            "write_delay_max": 20.0,
        }),
    }

    save_config(config, config_path)
    print("\n[4/4] 配置已保存到", config_path)
    return config


def start_scheduler(config_path: str = "config.json") -> None:
    """Start the APScheduler daemon for recurring transfers."""
    config = load_config(config_path)
    interval = config.get("task_schedule", {}).get("interval_hours", 24)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_transfer_job,
        trigger=IntervalTrigger(hours=interval),
        kwargs={"config_path": config_path},
        id="transfer_job",
        replace_existing=True,
        max_instances=1,
    )

    logger.info("Scheduler started: transfer every {} hours", interval)
    scheduler.start()
    return scheduler


async def async_daemon(config_path: str) -> None:
    """Run the transfer daemon.

    Uses 60-second polling with wall-clock time comparison instead of a single
    long asyncio.sleep.  This ensures the scheduled time is always respected
    even after Android Doze / SIGSTOP suspensions, where CLOCK_MONOTONIC stops
    advancing but CLOCK_REALTIME (datetime.now) continues.
    """
    config = load_config(config_path)
    interval_hours = config.get("task_schedule", {}).get("interval_hours", 24)

    logger.info("Daemon started: transfer every {} hours", interval_hours)
    print(f"\n定时任务已启动，每 {interval_hours} 小时执行一次，按 Ctrl+C 停止...")

    next_run_at = datetime.now(timezone.utc)  # trigger immediately on first iteration

    try:
        while True:
            if datetime.now(timezone.utc) >= next_run_at:
                # Re-read interval so config changes take effect without restart
                config = load_config(config_path)
                interval_hours = config.get("task_schedule", {}).get("interval_hours", 24)

                try:
                    await run_transfer_job(config_path)
                except Exception as e:
                    logger.error("Transfer job failed, daemon continues: {}", e, exc_info=True)

                next_run_at = datetime.now(timezone.utc) + timedelta(hours=interval_hours)
                logger.info(
                    "Next transfer at {} UTC (in {} hours)",
                    next_run_at.strftime("%Y-%m-%d %H:%M:%S"),
                    interval_hours,
                )

            # Short sleep — actual firing time governed by wall-clock above
            await asyncio.sleep(60)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n定时任务已停止")


def main() -> None:
    """Synchronous entry point with subcommand dispatch."""
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="B站收藏夹跨端迁移工具",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        metavar="PATH",
        help="配置文件路径（默认: config.json）",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    subparsers.add_parser("setup", help="首次设置：扫码登录两个账号并配置收藏夹ID")
    subparsers.add_parser("transfer", help="立即执行一次迁移")
    subparsers.add_parser("daemon", help="启动定时守护模式（先立即执行一次，再按间隔重复）")

    args = parser.parse_args()
    setup_logging()

    try:
        if args.command == "setup":
            asyncio.run(interactive_setup(args.config))
        elif args.command == "transfer":
            asyncio.run(run_transfer_job(args.config))
        elif args.command == "daemon":
            asyncio.run(async_daemon(args.config))
    except KeyboardInterrupt:
        print("\n程序已退出")


if __name__ == "__main__":
    main()
