"""Main entry point: CLI menu, scheduler, and orchestration."""

import asyncio
import sys

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.anti_ban import CircuitBreaker, RateLimiter
from src.api_endpoints import DEFAULT_HEADERS
from src.auth import login_account, refresh_cookie, _credentials_to_config_entry
from src.config import load_config, save_config, get_cookie_dict
from src.transfer import transfer_all

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


async def run_transfer_job(config_path: str = "config.json") -> dict:
    """Execute one complete transfer cycle.

    1. Load config
    2. Refresh cookies if needed
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
        # Refresh cookies if needed
        for account_key in ("sub_account", "main_account"):
            try:
                config = await refresh_cookie(client, account_key, config, config_path)
            except Exception as e:
                logger.warning("Cookie refresh failed for {}: {}", account_key, e)

        # Create rate limiter and circuit breaker
        rate_limiter = RateLimiter()
        circuit_breaker = CircuitBreaker(suspend_hours=4.0)

        # Run transfer
        tally = await transfer_all(client, config, rate_limiter, circuit_breaker)

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


async def async_main() -> None:
    """Async main menu loop."""
    setup_logging()

    while True:
        print("\n" + "=" * 50)
        print("  B站收藏夹跨端迁移工具")
        print("=" * 50)
        print("  1. 首次设置（扫码登录两个账号）")
        print("  2. 立即执行一次转移")
        print("  3. 启动定时守护模式")
        print("  4. 退出")
        print("=" * 50)

        choice = input("\n请选择操作 [1-4]: ").strip()

        if choice == "1":
            await interactive_setup()
        elif choice == "2":
            await run_transfer_job()
        elif choice == "3":
            scheduler = start_scheduler()
            print("\n定时任务已启动，按 Ctrl+C 停止...")
            try:
                # Run first job immediately, then let scheduler handle the rest
                await run_transfer_job()
                # Keep the event loop running for the scheduler
                while True:
                    await asyncio.sleep(60)
            except KeyboardInterrupt:
                scheduler.shutdown()
                print("\n定时任务已停止")
        elif choice == "4":
            print("再见！")
            break
        else:
            print("无效选择，请重试")


def main() -> None:
    """Synchronous entry point."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n程序已退出")


if __name__ == "__main__":
    main()
