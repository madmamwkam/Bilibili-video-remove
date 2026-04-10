"""Shared test fixtures for the entire test suite."""

import pytest


@pytest.fixture
def sample_config():
    """A fully populated config dict for testing."""
    return {
        "sub_account": {
            "cookie": "SESSDATA=sub_sess; bili_jct=sub_csrf; DedeUserID=111",
            "refresh_token": "sub_refresh_token_abc",
            "source_media_id": "12345",
        },
        "main_account": {
            "cookie": "SESSDATA=main_sess; bili_jct=main_csrf; DedeUserID=222",
            "refresh_token": "main_refresh_token_xyz",
            "target_media_id": "67890",
        },
        "task_schedule": {
            "interval_hours": 24,
        },
    }


@pytest.fixture
def empty_config():
    """A default/empty config skeleton."""
    return {
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
