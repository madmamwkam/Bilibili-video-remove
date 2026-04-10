"""Tests for config module."""

import json
import os

import pytest

from src.config import (
    build_cookie_string,
    get_cookie_dict,
    get_csrf,
    load_config,
    save_config,
)


@pytest.mark.timeout(5)
class TestLoadConfig:
    def test_load_missing_file_returns_defaults(self, tmp_path):
        config = load_config(str(tmp_path / "nonexistent.json"))
        assert config["sub_account"]["cookie"] == ""
        assert config["main_account"]["cookie"] == ""
        assert config["task_schedule"]["interval_hours"] == 24

    def test_load_existing_file(self, tmp_path):
        config_path = tmp_path / "config.json"
        data = {"sub_account": {"cookie": "test=1"}, "main_account": {}, "task_schedule": {}}
        config_path.write_text(json.dumps(data), encoding="utf-8")
        result = load_config(str(config_path))
        assert result["sub_account"]["cookie"] == "test=1"


@pytest.mark.timeout(5)
class TestSaveConfig:
    def test_save_and_load_roundtrip(self, tmp_path, sample_config):
        config_path = str(tmp_path / "config.json")
        save_config(sample_config, config_path)
        loaded = load_config(config_path)
        assert loaded == sample_config

    def test_save_overwrites_existing(self, tmp_path, sample_config):
        config_path = str(tmp_path / "config.json")
        save_config({"old": "data"}, config_path)
        save_config(sample_config, config_path)
        loaded = load_config(config_path)
        assert loaded == sample_config

    def test_atomic_write_no_tmp_file_left(self, tmp_path, sample_config):
        config_path = str(tmp_path / "config.json")
        save_config(sample_config, config_path)
        tmp_file = tmp_path / "config.json.tmp"
        assert not tmp_file.exists()

    def test_save_creates_valid_json(self, tmp_path, sample_config):
        config_path = tmp_path / "config.json"
        save_config(sample_config, str(config_path))
        raw = config_path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed == sample_config


@pytest.mark.timeout(5)
class TestCookieParsing:
    def test_get_cookie_dict_parses_correctly(self, sample_config):
        cookies = get_cookie_dict("sub_account", sample_config)
        assert cookies["SESSDATA"] == "sub_sess"
        assert cookies["bili_jct"] == "sub_csrf"
        assert cookies["DedeUserID"] == "111"

    def test_get_cookie_dict_empty_string(self, empty_config):
        cookies = get_cookie_dict("sub_account", empty_config)
        assert cookies == {}

    def test_get_cookie_dict_missing_account(self):
        cookies = get_cookie_dict("nonexistent", {})
        assert cookies == {}

    def test_build_cookie_string(self):
        cookie_dict = {"SESSDATA": "abc", "bili_jct": "def"}
        result = build_cookie_string(cookie_dict)
        assert "SESSDATA=abc" in result
        assert "bili_jct=def" in result
        assert "; " in result

    def test_roundtrip_cookie_parse_and_build(self, sample_config):
        cookies = get_cookie_dict("main_account", sample_config)
        rebuilt = build_cookie_string(cookies)
        # Re-parse should yield same dict
        reparsed = {}
        for pair in rebuilt.split(";"):
            k, v = pair.strip().split("=", 1)
            reparsed[k.strip()] = v.strip()
        assert reparsed == cookies


@pytest.mark.timeout(5)
class TestGetCsrf:
    def test_get_csrf_returns_bili_jct(self, sample_config):
        assert get_csrf("main_account", sample_config) == "main_csrf"
        assert get_csrf("sub_account", sample_config) == "sub_csrf"

    def test_get_csrf_empty_cookie(self, empty_config):
        assert get_csrf("main_account", empty_config) == ""
