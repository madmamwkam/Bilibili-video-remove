"""Configuration management for Bilibili favorites transfer tool."""

import json
import os
from pathlib import Path

from loguru import logger

DEFAULT_CONFIG = {
    "sub_account": {
        "cookie": "",
        "refresh_token": "",
        "source_media_id": "",
    },
    "main_account": {
        "cookie": "",
        "refresh_token": "",
        "target_media_id": "",
    },
    "task_schedule": {
        "interval_hours": 24,
    },
}


def load_config(path: str = "config.json") -> dict:
    """Load config from JSON file. Returns default skeleton if file is missing."""
    config_path = Path(path)
    if not config_path.exists():
        logger.info("Config file not found at {}, using defaults", path)
        return {**DEFAULT_CONFIG}
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    logger.info("Config loaded from {}", path)
    return config


def save_config(data: dict, path: str = "config.json") -> None:
    """Save config to JSON file atomically (write to .tmp, then rename)."""
    config_path = Path(path)
    tmp_path = config_path.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # Atomic rename (on Windows, need to remove target first if it exists)
    if config_path.exists():
        os.replace(str(tmp_path), str(config_path))
    else:
        tmp_path.rename(config_path)
    logger.info("Config saved to {}", path)


def get_cookie_dict(account_key: str, config: dict) -> dict:
    """Parse cookie string from config into a dict.

    Args:
        account_key: "main_account" or "sub_account"
        config: The full config dict

    Returns:
        Dict like {"SESSDATA": "xxx", "bili_jct": "yyy", "DedeUserID": "zzz"}
    """
    cookie_str = config.get(account_key, {}).get("cookie", "")
    if not cookie_str:
        return {}
    pairs = cookie_str.split(";")
    result = {}
    for pair in pairs:
        pair = pair.strip()
        if "=" in pair:
            key, value = pair.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def build_cookie_string(cookie_dict: dict) -> str:
    """Build cookie string from dict.

    Args:
        cookie_dict: Dict like {"SESSDATA": "xxx", "bili_jct": "yyy"}

    Returns:
        String like "SESSDATA=xxx; bili_jct=yyy"
    """
    return "; ".join(f"{k}={v}" for k, v in cookie_dict.items())


def get_csrf(account_key: str, config: dict) -> str:
    """Extract bili_jct (CSRF token) from account cookies."""
    cookies = get_cookie_dict(account_key, config)
    return cookies.get("bili_jct", "")
