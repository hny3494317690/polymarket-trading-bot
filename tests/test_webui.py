"""
Unit tests for the Web UI helper functions.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from apps.webui import build_env_map, save_env_file, validate_and_normalize_payload


def test_validate_and_normalize_payload_success():
    payload = {
        "private_key": "0x" + "a" * 64,
        "safe_address": "0x" + "b" * 40,
        "rpc_url": "https://polygon-rpc.com",
        "clob_host": "https://clob.polymarket.com",
        "chain_id": 137,
        "coin": "ETH",
        "size": 5,
        "drop_threshold": 0.3,
        "open_change_ranges_bps": "40-999 -999--40",
        "yes_price_min": 0.7,
        "yes_price_max": 0.9,
        "no_price_min": 0.1,
        "no_price_max": 0.3,
        "lookback": 10,
        "market_window_minutes": 5,
        "max_positions": 1,
        "take_profit_enabled": True,
        "take_profit": 0.1,
        "stop_loss_enabled": False,
        "stop_loss": 0.05,
    }

    settings, errors = validate_and_normalize_payload(payload)
    assert errors == []
    assert settings is not None
    assert settings["private_key"] == "0x" + "a" * 64
    assert settings["safe_address"] == "0x" + "b" * 40
    assert settings["market_window_minutes"] == 5
    assert settings["open_change_ranges_bps"] == "40-999 -999--40"
    assert settings["yes_price_min"] == 0.7
    assert settings["no_price_max"] == 0.3
    assert settings["take_profit"] == 0.1
    assert settings["stop_loss"] is None


def test_validate_and_normalize_payload_rejects_partial_builder_creds():
    payload = {
        "private_key": "0x" + "a" * 64,
        "safe_address": "0x" + "b" * 40,
        "builder_api_key": "key-only",
        "coin": "ETH",
        "size": 5,
        "drop_threshold": 0.3,
        "lookback": 10,
        "market_window_minutes": 15,
        "max_positions": 1,
        "take_profit_enabled": True,
        "take_profit": 0.1,
        "stop_loss_enabled": True,
        "stop_loss": 0.05,
    }

    settings, errors = validate_and_normalize_payload(payload)
    assert settings is None
    assert any("builder credentials" in err for err in errors)


def test_validate_and_normalize_payload_rejects_invalid_open_change_ranges():
    payload = {
        "private_key": "0x" + "a" * 64,
        "safe_address": "0x" + "b" * 40,
        "coin": "ETH",
        "size": 5,
        "drop_threshold": 0.3,
        "open_change_ranges_bps": "40to999",
        "lookback": 10,
        "market_window_minutes": 15,
        "max_positions": 1,
        "take_profit_enabled": True,
        "take_profit": 0.1,
        "stop_loss_enabled": True,
        "stop_loss": 0.05,
    }

    settings, errors = validate_and_normalize_payload(payload)
    assert settings is None
    assert any("open_change_ranges_bps" in err for err in errors)


def test_save_env_file_merges_and_removes_empty_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "POLY_PRIVATE_KEY=old\n"
        "POLY_SAFE_ADDRESS=0x1111111111111111111111111111111111111111\n"
        "EXTRA=keep\n",
        encoding="utf-8",
    )

    env_values = {
        "POLY_PRIVATE_KEY": "0x" + "a" * 64,
        "POLY_SAFE_ADDRESS": "0x" + "b" * 40,
        "POLY_BUILDER_API_KEY": "",
    }

    save_env_file(env_path, env_values)
    content = env_path.read_text(encoding="utf-8")

    assert "POLY_PRIVATE_KEY=0x" in content
    assert "POLY_SAFE_ADDRESS=0x" in content
    assert "EXTRA=keep" in content
    assert "POLY_BUILDER_API_KEY" not in content
    assert os.environ["POLY_PRIVATE_KEY"] == "0x" + "a" * 64


def test_build_env_map_contains_strategy_defaults():
    settings = {
        "private_key": "0x" + "a" * 64,
        "safe_address": "0x" + "b" * 40,
        "coin": "BTC",
        "size": 10,
        "drop_threshold": 0.2,
        "open_change_ranges_bps": "40-999 -999--40",
        "yes_price_min": 0.7,
        "yes_price_max": 0.9,
        "no_price_min": 0.1,
        "no_price_max": 0.3,
        "lookback": 12,
        "market_window_minutes": 5,
        "max_positions": 2,
        "take_profit_enabled": False,
        "take_profit": None,
        "stop_loss_enabled": True,
        "stop_loss": 0.08,
    }

    env_map = build_env_map(settings)
    assert env_map["POLY_UI_COIN"] == "BTC"
    assert env_map["POLY_UI_MARKET_WINDOW_MINUTES"] == "5"
    assert env_map["POLY_UI_OPEN_CHANGE_RANGES_BPS"] == "40-999 -999--40"
    assert env_map["POLY_UI_YES_MIN_PRICE"] == "0.7"
    assert env_map["POLY_UI_NO_MAX_PRICE"] == "0.3"
    assert env_map["POLY_UI_TP_ENABLED"] == "false"
    assert env_map["POLY_UI_TAKE_PROFIT"] == ""
    assert env_map["POLY_UI_STOP_LOSS"] == "0.08"
