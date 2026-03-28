#!/usr/bin/env python3
"""
Polymarket Trading Bot Web UI.

A local single-page web interface for configuring credentials and running the
Flash Crash strategy without manually editing .env files.

Run:
    python apps/webui.py
Then open:
    http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dotenv import dotenv_values, load_dotenv

import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.bot import TradingBot
from src.config import BuilderConfig, ClobConfig, Config
from src.utils import validate_address, validate_private_key
from strategies.flash_crash import FlashCrashConfig, FlashCrashStrategy

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ENV_FILE = ".env"

SUPPORTED_COINS = {"BTC", "ETH", "SOL", "XRP"}


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _to_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _format_env_value(value: str) -> str:
    """Quote env values when needed."""
    if value == "":
        return '""'

    needs_quotes = any(ch in value for ch in (" ", "#", "\t", "\n", '"'))
    if not needs_quotes:
        return value

    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def load_defaults_from_env() -> Dict[str, Any]:
    """Load non-sensitive defaults from current environment."""
    return {
        "safe_address": os.environ.get("POLY_SAFE_ADDRESS", ""),
        "rpc_url": os.environ.get("POLY_RPC_URL", "https://polygon-rpc.com"),
        "clob_host": os.environ.get("POLY_CLOB_HOST", "https://clob.polymarket.com"),
        "chain_id": int(os.environ.get("POLY_CHAIN_ID", "137")),
        "builder_api_key": os.environ.get("POLY_BUILDER_API_KEY", ""),
        "builder_api_secret": "",
        "builder_api_passphrase": os.environ.get("POLY_BUILDER_API_PASSPHRASE", ""),
        "data_dir": os.environ.get("POLY_DATA_DIR", "credentials"),
        "log_level": os.environ.get("POLY_LOG_LEVEL", "INFO"),
        "coin": os.environ.get("POLY_UI_COIN", "ETH").upper(),
        "size": float(os.environ.get("POLY_UI_SIZE", "5.0")),
        "drop_threshold": float(os.environ.get("POLY_UI_DROP_THRESHOLD", "0.30")),
        "open_change_ranges_bps": os.environ.get("POLY_UI_OPEN_CHANGE_RANGES_BPS", ""),
        "yes_price_min": _to_optional_float(os.environ.get("POLY_UI_YES_MIN_PRICE", "")),
        "yes_price_max": _to_optional_float(os.environ.get("POLY_UI_YES_MAX_PRICE", "")),
        "no_price_min": _to_optional_float(os.environ.get("POLY_UI_NO_MIN_PRICE", "")),
        "no_price_max": _to_optional_float(os.environ.get("POLY_UI_NO_MAX_PRICE", "")),
        "lookback": int(os.environ.get("POLY_UI_LOOKBACK_SECONDS", "10")),
        "market_window_minutes": int(os.environ.get("POLY_UI_MARKET_WINDOW_MINUTES", "15")),
        "max_positions": int(os.environ.get("POLY_UI_MAX_POSITIONS", "1")),
        "take_profit_enabled": _to_bool(os.environ.get("POLY_UI_TP_ENABLED", "true"), True),
        "take_profit": float(os.environ.get("POLY_UI_TAKE_PROFIT", "0.10")),
        "stop_loss_enabled": _to_bool(os.environ.get("POLY_UI_SL_ENABLED", "true"), True),
        "stop_loss": float(os.environ.get("POLY_UI_STOP_LOSS", "0.05")),
        "has_private_key": bool(os.environ.get("POLY_PRIVATE_KEY", "")),
    }


def validate_and_normalize_payload(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Validate request payload and normalize values for runtime."""
    errors: List[str] = []

    private_key_input = _clean_text(payload.get("private_key") or os.environ.get("POLY_PRIVATE_KEY", ""))
    key_ok, key_result = validate_private_key(private_key_input)
    if not key_ok:
        errors.append(f"private_key: {key_result}")

    safe_address = _clean_text(payload.get("safe_address") or os.environ.get("POLY_SAFE_ADDRESS", ""))
    if not validate_address(safe_address):
        errors.append("safe_address: must be a valid 0x Ethereum address")

    rpc_url = _clean_text(payload.get("rpc_url") or os.environ.get("POLY_RPC_URL", "https://polygon-rpc.com"))
    if not rpc_url.startswith("http"):
        errors.append("rpc_url: must start with http")

    clob_host = _clean_text(payload.get("clob_host") or os.environ.get("POLY_CLOB_HOST", "https://clob.polymarket.com"))
    if not clob_host.startswith("http"):
        errors.append("clob_host: must start with http")

    try:
        chain_id = _to_int(payload.get("chain_id", os.environ.get("POLY_CHAIN_ID", 137)), 137)
        if chain_id <= 0:
            errors.append("chain_id: must be greater than 0")
    except ValueError:
        errors.append("chain_id: must be an integer")
        chain_id = 137

    builder_api_key = _clean_text(payload.get("builder_api_key") or os.environ.get("POLY_BUILDER_API_KEY", ""))
    builder_api_secret = _clean_text(payload.get("builder_api_secret") or os.environ.get("POLY_BUILDER_API_SECRET", ""))
    builder_api_passphrase = _clean_text(
        payload.get("builder_api_passphrase") or os.environ.get("POLY_BUILDER_API_PASSPHRASE", "")
    )

    builder_fields = [builder_api_key, builder_api_secret, builder_api_passphrase]
    if any(builder_fields) and not all(builder_fields):
        errors.append("builder credentials: fill all 3 fields or leave all empty")

    try:
        coin = _clean_text(payload.get("coin", "ETH")).upper()
        if coin not in SUPPORTED_COINS:
            errors.append(f"coin: must be one of {sorted(SUPPORTED_COINS)}")

        size = _to_float(payload.get("size", 5.0), 5.0)
        if size <= 0:
            errors.append("size: must be greater than 0")

        drop_threshold = _to_float(payload.get("drop_threshold", 0.30), 0.30)
        if drop_threshold <= 0:
            errors.append("drop_threshold: must be greater than 0")

        open_change_ranges_bps = _clean_text(payload.get("open_change_ranges_bps", ""))
        if open_change_ranges_bps:
            try:
                FlashCrashStrategy.parse_change_ranges_bps(open_change_ranges_bps)
            except ValueError as exc:
                errors.append(f"open_change_ranges_bps: {exc}")

        yes_price_min = _to_optional_float(payload.get("yes_price_min"))
        yes_price_max = _to_optional_float(payload.get("yes_price_max"))
        no_price_min = _to_optional_float(payload.get("no_price_min"))
        no_price_max = _to_optional_float(payload.get("no_price_max"))

        for name, val in [
            ("yes_price_min", yes_price_min),
            ("yes_price_max", yes_price_max),
            ("no_price_min", no_price_min),
            ("no_price_max", no_price_max),
        ]:
            if val is not None and not (0 <= val <= 1):
                errors.append(f"{name}: must be in [0, 1]")

        if yes_price_min is not None and yes_price_max is not None and yes_price_min > yes_price_max:
            errors.append("yes_price_min: cannot be greater than yes_price_max")
        if no_price_min is not None and no_price_max is not None and no_price_min > no_price_max:
            errors.append("no_price_min: cannot be greater than no_price_max")

        lookback = _to_int(payload.get("lookback", 10), 10)
        if lookback <= 0:
            errors.append("lookback: must be greater than 0")

        market_window_minutes = _to_int(payload.get("market_window_minutes", 15), 15)
        if market_window_minutes <= 0:
            errors.append("market_window_minutes: must be greater than 0")

        max_positions = _to_int(payload.get("max_positions", 1), 1)
        if max_positions <= 0:
            errors.append("max_positions: must be greater than 0")

        tp_enabled = _to_bool(payload.get("take_profit_enabled", True), True)
        sl_enabled = _to_bool(payload.get("stop_loss_enabled", True), True)

        take_profit = _to_float(payload.get("take_profit", 0.10), 0.10)
        stop_loss = _to_float(payload.get("stop_loss", 0.05), 0.05)

        if tp_enabled and take_profit <= 0:
            errors.append("take_profit: must be greater than 0 when enabled")
        if sl_enabled and stop_loss <= 0:
            errors.append("stop_loss: must be greater than 0 when enabled")

        data_dir = _clean_text(payload.get("data_dir") or os.environ.get("POLY_DATA_DIR", "credentials"))
        log_level = _clean_text(payload.get("log_level") or os.environ.get("POLY_LOG_LEVEL", "INFO")).upper()

    except ValueError as exc:
        errors.append(f"numeric fields: {exc}")
        coin = "ETH"
        size = 5.0
        drop_threshold = 0.30
        open_change_ranges_bps = ""
        yes_price_min = None
        yes_price_max = None
        no_price_min = None
        no_price_max = None
        lookback = 10
        market_window_minutes = 15
        max_positions = 1
        tp_enabled = True
        sl_enabled = True
        take_profit = 0.10
        stop_loss = 0.05
        data_dir = "credentials"
        log_level = "INFO"

    if errors:
        return None, errors

    assert key_ok

    normalized = {
        "private_key": key_result,
        "safe_address": safe_address.lower(),
        "rpc_url": rpc_url,
        "clob_host": clob_host,
        "chain_id": chain_id,
        "builder_api_key": builder_api_key,
        "builder_api_secret": builder_api_secret,
        "builder_api_passphrase": builder_api_passphrase,
        "coin": coin,
        "size": size,
        "drop_threshold": drop_threshold,
        "open_change_ranges_bps": open_change_ranges_bps,
        "yes_price_min": yes_price_min,
        "yes_price_max": yes_price_max,
        "no_price_min": no_price_min,
        "no_price_max": no_price_max,
        "lookback": lookback,
        "market_window_minutes": market_window_minutes,
        "max_positions": max_positions,
        "take_profit_enabled": tp_enabled,
        "stop_loss_enabled": sl_enabled,
        "take_profit": take_profit if tp_enabled else None,
        "stop_loss": stop_loss if sl_enabled else None,
        "data_dir": data_dir,
        "log_level": log_level,
    }
    return normalized, []


def build_env_map(settings: Dict[str, Any]) -> Dict[str, str]:
    """Build .env key-value map from normalized settings."""
    return {
        "POLY_PRIVATE_KEY": str(settings.get("private_key", "")),
        "POLY_SAFE_ADDRESS": str(settings.get("safe_address", "")),
        "POLY_RPC_URL": str(settings.get("rpc_url", "https://polygon-rpc.com")),
        "POLY_CLOB_HOST": str(settings.get("clob_host", "https://clob.polymarket.com")),
        "POLY_CHAIN_ID": str(settings.get("chain_id", 137)),
        "POLY_BUILDER_API_KEY": str(settings.get("builder_api_key", "")),
        "POLY_BUILDER_API_SECRET": str(settings.get("builder_api_secret", "")),
        "POLY_BUILDER_API_PASSPHRASE": str(settings.get("builder_api_passphrase", "")),
        "POLY_DATA_DIR": str(settings.get("data_dir", "credentials")),
        "POLY_LOG_LEVEL": str(settings.get("log_level", "INFO")),
        "POLY_UI_COIN": str(settings.get("coin", "ETH")),
        "POLY_UI_SIZE": str(settings.get("size", 5.0)),
        "POLY_UI_DROP_THRESHOLD": str(settings.get("drop_threshold", 0.30)),
        "POLY_UI_OPEN_CHANGE_RANGES_BPS": str(settings.get("open_change_ranges_bps", "")),
        "POLY_UI_YES_MIN_PRICE": "" if settings.get("yes_price_min") is None else str(settings.get("yes_price_min")),
        "POLY_UI_YES_MAX_PRICE": "" if settings.get("yes_price_max") is None else str(settings.get("yes_price_max")),
        "POLY_UI_NO_MIN_PRICE": "" if settings.get("no_price_min") is None else str(settings.get("no_price_min")),
        "POLY_UI_NO_MAX_PRICE": "" if settings.get("no_price_max") is None else str(settings.get("no_price_max")),
        "POLY_UI_LOOKBACK_SECONDS": str(settings.get("lookback", 10)),
        "POLY_UI_MARKET_WINDOW_MINUTES": str(settings.get("market_window_minutes", 15)),
        "POLY_UI_MAX_POSITIONS": str(settings.get("max_positions", 1)),
        "POLY_UI_TP_ENABLED": "true" if settings.get("take_profit_enabled", True) else "false",
        "POLY_UI_SL_ENABLED": "true" if settings.get("stop_loss_enabled", True) else "false",
        "POLY_UI_TAKE_PROFIT": "" if settings.get("take_profit") is None else str(settings.get("take_profit")),
        "POLY_UI_STOP_LOSS": "" if settings.get("stop_loss") is None else str(settings.get("stop_loss")),
    }


def save_env_file(env_path: Path, env_values: Dict[str, str]) -> None:
    """Merge and persist environment values into .env."""
    existing: Dict[str, str] = {}
    if env_path.exists():
        existing = {k: (v or "") for k, v in dotenv_values(env_path).items()}

    for key, value in env_values.items():
        if value == "":
            existing.pop(key, None)
        else:
            existing[key] = value

    ordered_prefix = [
        "POLY_PRIVATE_KEY",
        "POLY_SAFE_ADDRESS",
        "POLY_RPC_URL",
        "POLY_CLOB_HOST",
        "POLY_CHAIN_ID",
        "POLY_BUILDER_API_KEY",
        "POLY_BUILDER_API_SECRET",
        "POLY_BUILDER_API_PASSPHRASE",
        "POLY_DATA_DIR",
        "POLY_LOG_LEVEL",
        "POLY_UI_COIN",
        "POLY_UI_SIZE",
        "POLY_UI_DROP_THRESHOLD",
        "POLY_UI_OPEN_CHANGE_RANGES_BPS",
        "POLY_UI_YES_MIN_PRICE",
        "POLY_UI_YES_MAX_PRICE",
        "POLY_UI_NO_MIN_PRICE",
        "POLY_UI_NO_MAX_PRICE",
        "POLY_UI_LOOKBACK_SECONDS",
        "POLY_UI_MARKET_WINDOW_MINUTES",
        "POLY_UI_MAX_POSITIONS",
        "POLY_UI_TP_ENABLED",
        "POLY_UI_SL_ENABLED",
        "POLY_UI_TAKE_PROFIT",
        "POLY_UI_STOP_LOSS",
    ]

    keys = [k for k in ordered_prefix if k in existing]
    keys.extend(sorted(k for k in existing if k not in ordered_prefix))

    lines = [f"{key}={_format_env_value(existing[key])}" for key in keys]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for key, value in env_values.items():
        if value == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class WebFlashCrashStrategy(FlashCrashStrategy):
    """FlashCrash strategy variant that is quiet in terminal and reports to Web UI."""

    def __init__(self, bot: TradingBot, config: FlashCrashConfig, event_logger):
        super().__init__(bot, config)
        self._event_logger = event_logger
        self.last_prices: Dict[str, float] = {}

    def log(self, msg: str, level: str = "info") -> None:
        self._event_logger(level, msg)
        if self._status_mode:
            self._log_buffer.add(msg, level)

    def render_status(self, prices: Dict[str, float]) -> None:
        self.last_prices = dict(prices)


@dataclass
class RunnerSnapshot:
    running: bool
    started_at: Optional[float]
    coin: Optional[str]
    market_window_minutes: Optional[int]
    ws_connected: bool
    market_slug: str
    market_question: str
    countdown: str
    position_count: int
    stats: Dict[str, Any]
    positions: List[Dict[str, Any]]
    last_error: str
    logs: List[Dict[str, str]]


class WebStrategyRunner:
    """Background strategy runner managed by HTTP endpoints."""

    def __init__(self, env_path: Path):
        self.env_path = env_path
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._strategy: Optional[WebFlashCrashStrategy] = None
        self._running = False
        self._started_at: Optional[float] = None
        self._coin: Optional[str] = None
        self._market_window_minutes: Optional[int] = None
        self._last_error = ""
        self._logs: Deque[Dict[str, str]] = deque(maxlen=300)

    def append_log(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {
            "time": ts,
            "level": level,
            "message": message,
        }
        with self._lock:
            self._logs.append(entry)

    def _build_bot(self, settings: Dict[str, Any]) -> TradingBot:
        builder = BuilderConfig(
            api_key=settings["builder_api_key"],
            api_secret=settings["builder_api_secret"],
            api_passphrase=settings["builder_api_passphrase"],
        )

        config = Config(
            safe_address=settings["safe_address"],
            rpc_url=settings["rpc_url"],
            clob=ClobConfig(host=settings["clob_host"], chain_id=settings["chain_id"]),
            builder=builder,
            data_dir=settings["data_dir"],
            log_level=settings["log_level"],
        )

        errors = config.validate()
        if errors:
            raise ValueError("; ".join(errors))

        bot = TradingBot(config=config, private_key=settings["private_key"])
        if not bot.is_initialized():
            raise RuntimeError("Failed to initialize TradingBot with current settings")

        return bot

    def _run_thread(self, settings: Dict[str, Any]) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        with self._lock:
            self._loop = loop

        try:
            bot = self._build_bot(settings)
            strategy_config = FlashCrashConfig(
                coin=settings["coin"],
                size=settings["size"],
                max_positions=settings["max_positions"],
                take_profit=settings["take_profit"],
                stop_loss=settings["stop_loss"],
                market_window_minutes=settings["market_window_minutes"],
                price_lookback_seconds=settings["lookback"],
                drop_threshold=settings["drop_threshold"],
                open_change_ranges_bps=settings["open_change_ranges_bps"],
                yes_price_min=settings["yes_price_min"],
                yes_price_max=settings["yes_price_max"],
                no_price_min=settings["no_price_min"],
                no_price_max=settings["no_price_max"],
            )
            strategy = WebFlashCrashStrategy(bot=bot, config=strategy_config, event_logger=self.append_log)

            with self._lock:
                self._strategy = strategy
                self._coin = settings["coin"]
                self._market_window_minutes = settings["market_window_minutes"]
                self._started_at = time.time()

            self.append_log("success", "Strategy event loop started")
            loop.run_until_complete(strategy.run())
            self.append_log("info", "Strategy event loop exited")
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            self.append_log("error", f"Runner error: {exc}")
            self.append_log("debug", traceback.format_exc(limit=8))
        finally:
            with self._lock:
                self._running = False
                self._thread = None
                self._loop = None
                self._strategy = None

            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                loop.close()

    def start(self, settings: Dict[str, Any]) -> Tuple[bool, str]:
        with self._lock:
            if self._running:
                return False, "Strategy is already running"
            self._running = True
            self._last_error = ""

        self.append_log(
            "info",
            (
                "Starting strategy: "
                f"coin={settings['coin']} window={settings['market_window_minutes']}m "
                f"size=${settings['size']:.2f} drop={settings['drop_threshold']:.2f}"
            ),
        )

        thread = threading.Thread(target=self._run_thread, args=(settings,), daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()

        return True, "Strategy started"

    def stop(self, timeout: float = 10.0) -> Tuple[bool, str]:
        with self._lock:
            running = self._running
            loop = self._loop
            strategy = self._strategy
            thread = self._thread

        if not running:
            return False, "Strategy is not running"

        self.append_log("warning", "Stop requested")

        if loop and strategy:
            try:
                loop.call_soon_threadsafe(setattr, strategy, "running", False)
            except Exception:
                pass

        if thread:
            thread.join(timeout=timeout)
            if thread.is_alive():
                return False, "Stop timed out; strategy thread is still running"

        return True, "Strategy stopped"

    def snapshot(self) -> RunnerSnapshot:
        with self._lock:
            running = self._running
            strategy = self._strategy
            logs = list(self._logs)[-200:]
            started_at = self._started_at
            coin = self._coin
            market_window_minutes = self._market_window_minutes
            last_error = self._last_error

        ws_connected = False
        market_slug = ""
        market_question = ""
        countdown = "--:--"
        position_count = 0
        stats: Dict[str, Any] = {
            "trades_opened": 0,
            "trades_closed": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
        }
        positions: List[Dict[str, Any]] = []

        if strategy is not None:
            try:
                ws_connected = strategy.is_connected

                market = strategy.current_market
                if market:
                    market_slug = market.slug
                    market_question = market.question
                    countdown = market.get_countdown_str()

                stats = strategy.positions.get_stats()
                prices = strategy.last_prices

                for pos in strategy.positions.get_all_positions():
                    current = float(prices.get(pos.side, 0.0))
                    pnl = pos.get_pnl(current) if current > 0 else 0.0
                    positions.append(
                        {
                            "id": pos.id,
                            "side": pos.side,
                            "entry_price": pos.entry_price,
                            "current_price": current,
                            "size": pos.size,
                            "pnl": pnl,
                            "take_profit_price": pos.take_profit_price,
                            "stop_loss_price": pos.stop_loss_price,
                            "hold_seconds": round(pos.get_hold_time(), 1),
                        }
                    )
            except Exception as exc:
                last_error = str(exc)

        position_count = len(positions)

        return RunnerSnapshot(
            running=running,
            started_at=started_at,
            coin=coin,
            market_window_minutes=market_window_minutes,
            ws_connected=ws_connected,
            market_slug=market_slug,
            market_question=market_question,
            countdown=countdown,
            position_count=position_count,
            stats=stats,
            positions=positions,
            last_error=last_error,
            logs=logs,
        )


WEB_UI_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polymarket 机器人 Web 界面</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

    :root {
      --bg-1: #f7f5ef;
      --bg-2: #e8f2ff;
      --ink: #111827;
      --ink-soft: #334155;
      --panel: rgba(255, 255, 255, 0.88);
      --accent: #0f766e;
      --accent-2: #c2410c;
      --ok: #15803d;
      --warn: #b45309;
      --bad: #b91c1c;
      --line: rgba(17, 24, 39, 0.1);
      --mono: "IBM Plex Mono", Menlo, Monaco, Consolas, monospace;
      --sans: "Space Grotesk", "Trebuchet MS", "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: var(--sans);
      color: var(--ink);
      background:
        radial-gradient(1200px 500px at 10% -5%, rgba(15, 118, 110, 0.2), transparent 60%),
        radial-gradient(900px 400px at 90% 0%, rgba(194, 65, 12, 0.2), transparent 60%),
        linear-gradient(145deg, var(--bg-1), var(--bg-2));
      min-height: 100vh;
    }

    .shell {
      width: min(1180px, 96vw);
      margin: 20px auto 40px;
      display: grid;
      grid-template-columns: 1.3fr 1fr;
      gap: 14px;
    }

    .hero {
      grid-column: 1 / -1;
      background: linear-gradient(120deg, rgba(15, 118, 110, 0.92), rgba(3, 105, 161, 0.92));
      color: #f8fafc;
      border-radius: 16px;
      padding: 18px 20px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.15);
      position: relative;
      overflow: hidden;
    }

    .hero::after {
      content: "";
      position: absolute;
      right: -45px;
      top: -45px;
      width: 210px;
      height: 210px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.12);
      transform: rotate(24deg);
    }

    h1 {
      margin: 0;
      font-size: clamp(1.3rem, 1.8vw, 1.9rem);
      letter-spacing: 0.4px;
    }

    .hero p {
      margin: 8px 0 0;
      opacity: 0.95;
      font-size: 0.95rem;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
      backdrop-filter: blur(6px);
      animation: rise 220ms ease;
    }

    @keyframes rise {
      from { transform: translateY(6px); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }

    .panel h2 {
      margin: 0 0 10px;
      font-size: 1.05rem;
      color: var(--ink-soft);
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }

    .field {
      display: flex;
      flex-direction: column;
      gap: 5px;
    }

    .field.full {
      grid-column: 1 / -1;
    }

    label {
      font-size: 0.8rem;
      color: #475569;
      font-weight: 600;
      letter-spacing: 0.2px;
    }

    input, select {
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 10px;
      padding: 10px 11px;
      font-size: 0.92rem;
      font-family: var(--sans);
      background: #ffffff;
      color: var(--ink);
      outline: none;
      transition: border-color 120ms ease, box-shadow 120ms ease;
    }

    input:focus, select:focus {
      border-color: #0369a1;
      box-shadow: 0 0 0 3px rgba(14, 116, 144, 0.15);
    }

    .checkbox {
      display: flex;
      align-items: center;
      gap: 8px;
      padding-top: 4px;
      font-size: 0.9rem;
      color: #334155;
    }

    .checkbox input {
      width: auto;
      margin: 0;
    }

    .actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 12px;
    }

    button {
      border: 0;
      border-radius: 999px;
      padding: 10px 14px;
      font-family: var(--sans);
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      transition: transform 80ms ease, box-shadow 120ms ease, opacity 120ms ease;
    }

    button:hover { transform: translateY(-1px); }
    button:active { transform: translateY(0); }

    .btn-save {
      background: linear-gradient(120deg, #0f766e, #0e7490);
      color: #fff;
      box-shadow: 0 8px 18px rgba(15, 118, 110, 0.25);
    }

    .btn-start {
      background: linear-gradient(120deg, #15803d, #0f766e);
      color: #fff;
      box-shadow: 0 8px 18px rgba(21, 128, 61, 0.25);
    }

    .btn-stop {
      background: linear-gradient(120deg, #b91c1c, #be123c);
      color: #fff;
      box-shadow: 0 8px 18px rgba(185, 28, 28, 0.25);
    }

    .btn-refresh {
      background: #e2e8f0;
      color: #0f172a;
    }

    .banner {
      margin-top: 10px;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 0.9rem;
      display: none;
    }

    .banner.ok { display: block; background: rgba(22, 163, 74, 0.12); color: #14532d; }
    .banner.warn { display: block; background: rgba(245, 158, 11, 0.15); color: #78350f; }
    .banner.bad { display: block; background: rgba(220, 38, 38, 0.12); color: #7f1d1d; }

    .status-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 10px;
    }

    .pill {
      border-radius: 10px;
      padding: 9px;
      border: 1px solid var(--line);
      background: #f8fafc;
      font-size: 0.86rem;
    }

    .pill b {
      display: block;
      margin-bottom: 2px;
      font-size: 0.77rem;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }

    .logs {
      margin-top: 8px;
      border: 1px solid #cbd5e1;
      border-radius: 12px;
      background: #0f172a;
      color: #e2e8f0;
      font-family: var(--mono);
      font-size: 0.78rem;
      line-height: 1.45;
      height: 250px;
      overflow: auto;
      padding: 10px;
      white-space: pre-wrap;
      word-break: break-word;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.84rem;
      margin-top: 6px;
    }

    th, td {
      border-bottom: 1px solid #e2e8f0;
      padding: 6px;
      text-align: left;
    }

    th { color: #475569; font-weight: 700; }

    .mono { font-family: var(--mono); }

    @media (max-width: 980px) {
      .shell { grid-template-columns: 1fr; }
      .status-grid { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <h1>Polymarket 交易机器人 Web 界面</h1>
      <p>在浏览器中填写参数、保存配置并启动策略。无需手动编辑 .env。</p>
    </div>

    <section class="panel">
      <h2>凭证与网络</h2>
      <div class="grid">
        <div class="field full">
          <label for="private_key">私钥</label>
          <input id="private_key" type="password" placeholder="0x...（留空将复用当前环境中的私钥）" autocomplete="off" />
        </div>
        <div class="field full">
          <label for="safe_address">Safe 地址</label>
          <input id="safe_address" type="text" placeholder="0x..." />
        </div>
        <div class="field full">
          <label for="rpc_url">RPC 地址</label>
          <input id="rpc_url" type="text" placeholder="https://polygon-rpc.com" />
        </div>
        <div class="field">
          <label for="clob_host">CLOB 地址</label>
          <input id="clob_host" type="text" placeholder="https://clob.polymarket.com" />
        </div>
        <div class="field">
          <label for="chain_id">链 ID</label>
          <input id="chain_id" type="number" min="1" step="1" value="137" />
        </div>
        <div class="field full">
          <label for="builder_api_key">Builder API Key（可选）</label>
          <input id="builder_api_key" type="text" placeholder="无 Gas 模式可选" />
        </div>
        <div class="field">
          <label for="builder_api_secret">Builder API Secret</label>
          <input id="builder_api_secret" type="password" placeholder="可选" autocomplete="off" />
        </div>
        <div class="field">
          <label for="builder_api_passphrase">Builder Passphrase</label>
          <input id="builder_api_passphrase" type="text" placeholder="可选" />
        </div>
        <div class="field">
          <label for="data_dir">数据目录</label>
          <input id="data_dir" type="text" value="credentials" />
        </div>
        <div class="field">
          <label for="log_level">日志级别</label>
          <select id="log_level">
            <option value="DEBUG">DEBUG</option>
            <option value="INFO" selected>INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </select>
        </div>
      </div>

      <h2 style="margin-top:14px;">策略参数</h2>
      <div class="grid">
        <div class="field">
          <label for="coin">币种</label>
          <select id="coin">
            <option>BTC</option>
            <option selected>ETH</option>
            <option>SOL</option>
            <option>XRP</option>
          </select>
        </div>
        <div class="field">
          <label for="market_window_minutes">市场窗口（分钟）</label>
          <input id="market_window_minutes" type="number" min="1" step="1" value="15" />
        </div>
        <div class="field">
          <label for="size">交易金额（USDC）</label>
          <input id="size" type="number" min="0.01" step="0.01" value="5" />
        </div>
        <div class="field">
          <label for="max_positions">最大持仓数</label>
          <input id="max_positions" type="number" min="1" step="1" value="1" />
        </div>
        <div class="field">
          <label for="drop_threshold">闪崩触发阈值（检测）</label>
          <input id="drop_threshold" type="number" min="0.01" step="0.01" value="0.30" />
        </div>
        <div class="field">
          <label for="lookback">回看窗口（秒）</label>
          <input id="lookback" type="number" min="1" step="1" value="10" />
        </div>
        <div class="field full">
          <label for="open_change_ranges_bps">相对开盘变化区间（bps，可多段）</label>
          <input id="open_change_ranges_bps" type="text" placeholder="例如：40-999 -999--40（留空=不过滤）" />
        </div>
        <div class="field">
          <label for="yes_price_min">YES 最低价（可选）</label>
          <input id="yes_price_min" type="number" min="0" max="1" step="0.0001" placeholder="例如 0.70" />
        </div>
        <div class="field">
          <label for="yes_price_max">YES 最高价（可选）</label>
          <input id="yes_price_max" type="number" min="0" max="1" step="0.0001" placeholder="例如 0.90" />
        </div>
        <div class="field">
          <label for="no_price_min">NO 最低价（可选）</label>
          <input id="no_price_min" type="number" min="0" max="1" step="0.0001" placeholder="例如 0.10" />
        </div>
        <div class="field">
          <label for="no_price_max">NO 最高价（可选）</label>
          <input id="no_price_max" type="number" min="0" max="1" step="0.0001" placeholder="例如 0.30" />
        </div>

        <div class="field full">
          <label class="checkbox"><input id="take_profit_enabled" type="checkbox" checked /> 启用止盈</label>
        </div>
        <div class="field">
          <label for="take_profit">止盈值</label>
          <input id="take_profit" type="number" min="0.01" step="0.01" value="0.10" />
        </div>
        <div class="field"></div>

        <div class="field full">
          <label class="checkbox"><input id="stop_loss_enabled" type="checkbox" checked /> 启用止损</label>
        </div>
        <div class="field">
          <label for="stop_loss">止损值</label>
          <input id="stop_loss" type="number" min="0.01" step="0.01" value="0.05" />
        </div>
      </div>

      <div class="actions">
        <button class="btn-save" id="save_btn" type="button">保存配置</button>
        <button class="btn-start" id="start_btn" type="button">启动策略</button>
        <button class="btn-stop" id="stop_btn" type="button">停止策略</button>
        <button class="btn-refresh" id="refresh_btn" type="button">刷新状态</button>
      </div>
      <div id="banner" class="banner"></div>
    </section>

    <section class="panel">
      <h2>运行状态</h2>
      <div class="status-grid" id="status_grid"></div>

      <h2>当前持仓</h2>
      <table>
        <thead>
          <tr>
            <th>编号</th>
            <th>方向</th>
            <th>开仓价</th>
            <th>当前价</th>
            <th>数量</th>
            <th>PnL</th>
          </tr>
        </thead>
        <tbody id="positions_body"></tbody>
      </table>

      <h2 style="margin-top:12px;">日志</h2>
      <div class="logs mono" id="logs"></div>
    </section>
  </div>

  <script>
    const statusGrid = document.getElementById("status_grid");
    const positionsBody = document.getElementById("positions_body");
    const logsPanel = document.getElementById("logs");
    const banner = document.getElementById("banner");

    function el(id) {
      return document.getElementById(id);
    }

    function nval(id, fallback) {
      const raw = el(id).value;
      const num = Number(raw);
      return Number.isFinite(num) ? num : fallback;
    }

    function oval(id) {
      const raw = el(id).value.trim();
      if (raw === "") return null;
      const num = Number(raw);
      return Number.isFinite(num) ? num : null;
    }

    function showBanner(type, message) {
      banner.className = `banner ${type}`;
      banner.textContent = message;
    }

    function clearBanner() {
      banner.className = "banner";
      banner.textContent = "";
    }

    function setTpSlEnabledStates() {
      el("take_profit").disabled = !el("take_profit_enabled").checked;
      el("stop_loss").disabled = !el("stop_loss_enabled").checked;
    }

    function payloadFromForm() {
      return {
        private_key: el("private_key").value.trim(),
        safe_address: el("safe_address").value.trim(),
        rpc_url: el("rpc_url").value.trim(),
        clob_host: el("clob_host").value.trim(),
        chain_id: nval("chain_id", 137),
        builder_api_key: el("builder_api_key").value.trim(),
        builder_api_secret: el("builder_api_secret").value.trim(),
        builder_api_passphrase: el("builder_api_passphrase").value.trim(),
        data_dir: el("data_dir").value.trim(),
        log_level: el("log_level").value,
        coin: el("coin").value,
        size: nval("size", 5.0),
        max_positions: nval("max_positions", 1),
        drop_threshold: nval("drop_threshold", 0.30),
        open_change_ranges_bps: el("open_change_ranges_bps").value.trim(),
        yes_price_min: oval("yes_price_min"),
        yes_price_max: oval("yes_price_max"),
        no_price_min: oval("no_price_min"),
        no_price_max: oval("no_price_max"),
        lookback: nval("lookback", 10),
        market_window_minutes: nval("market_window_minutes", 15),
        take_profit_enabled: el("take_profit_enabled").checked,
        stop_loss_enabled: el("stop_loss_enabled").checked,
        take_profit: nval("take_profit", 0.10),
        stop_loss: nval("stop_loss", 0.05),
      };
    }

    async function postJson(path, payload) {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || data.message || `Request failed: ${res.status}`);
      }
      return data;
    }

    function fillDefaults(defaults) {
      if (!defaults) return;

      el("safe_address").value = defaults.safe_address || "";
      el("rpc_url").value = defaults.rpc_url || "https://polygon-rpc.com";
      el("clob_host").value = defaults.clob_host || "https://clob.polymarket.com";
      el("chain_id").value = defaults.chain_id || 137;
      el("builder_api_key").value = defaults.builder_api_key || "";
      el("builder_api_passphrase").value = defaults.builder_api_passphrase || "";
      el("data_dir").value = defaults.data_dir || "credentials";
      el("log_level").value = defaults.log_level || "INFO";
      el("coin").value = defaults.coin || "ETH";
      el("size").value = defaults.size || 5.0;
      el("drop_threshold").value = defaults.drop_threshold || 0.30;
      el("open_change_ranges_bps").value = defaults.open_change_ranges_bps || "";
      el("yes_price_min").value = defaults.yes_price_min ?? "";
      el("yes_price_max").value = defaults.yes_price_max ?? "";
      el("no_price_min").value = defaults.no_price_min ?? "";
      el("no_price_max").value = defaults.no_price_max ?? "";
      el("lookback").value = defaults.lookback || 10;
      el("market_window_minutes").value = defaults.market_window_minutes || 15;
      el("max_positions").value = defaults.max_positions || 1;
      el("take_profit_enabled").checked = defaults.take_profit_enabled !== false;
      el("take_profit").value = defaults.take_profit || 0.10;
      el("stop_loss_enabled").checked = defaults.stop_loss_enabled !== false;
      el("stop_loss").value = defaults.stop_loss || 0.05;
      setTpSlEnabledStates();

      if (defaults.has_private_key) {
        showBanner("ok", "检测到环境中已有私钥，可留空以复用。");
      }
    }

    function renderStatus(data) {
      const snapshot = data.snapshot || {};
      const stats = snapshot.stats || {};

      const started = snapshot.started_at
        ? new Date(snapshot.started_at * 1000).toLocaleTimeString()
        : "-";

      const pills = [
        { key: "策略状态", value: snapshot.running ? "运行中" : "已停止" },
        { key: "币种", value: snapshot.coin || "-" },
        { key: "窗口", value: snapshot.market_window_minutes ? `${snapshot.market_window_minutes}m` : "-" },
        { key: "WebSocket", value: snapshot.ws_connected ? "已连接" : "未连接" },
        { key: "倒计时", value: snapshot.countdown || "--:--" },
        { key: "启动时间", value: started },
        { key: "交易统计", value: `${stats.trades_closed || 0} 平 / ${stats.trades_opened || 0} 开` },
        { key: "总盈亏", value: Number(stats.total_pnl || 0).toFixed(2) },
        { key: "胜率", value: `${Number(stats.win_rate || 0).toFixed(1)}%` },
      ];

      statusGrid.innerHTML = pills
        .map((p) => `<div class="pill"><b>${p.key}</b><span>${p.value}</span></div>`)
        .join("");

      positionsBody.innerHTML = "";
      const positions = snapshot.positions || [];
      if (!positions.length) {
        positionsBody.innerHTML = `<tr><td colspan="6">暂无持仓</td></tr>`;
      } else {
        for (const pos of positions) {
          const row = document.createElement("tr");
          row.innerHTML = `
            <td class="mono">${pos.id}</td>
            <td>${String(pos.side || "").toUpperCase()}</td>
            <td>${Number(pos.entry_price || 0).toFixed(4)}</td>
            <td>${Number(pos.current_price || 0).toFixed(4)}</td>
            <td>${Number(pos.size || 0).toFixed(2)}</td>
            <td>${Number(pos.pnl || 0).toFixed(2)}</td>
          `;
          positionsBody.appendChild(row);
        }
      }

      const logs = snapshot.logs || [];
      logsPanel.textContent = logs
        .map((x) => `[${x.time}] ${String(x.level || "info").toUpperCase()} ${x.message}`)
        .join("\n");
      logsPanel.scrollTop = logsPanel.scrollHeight;

      if (snapshot.last_error) {
        showBanner("bad", `策略错误：${snapshot.last_error}`);
      }
    }

    async function refreshStatus() {
      try {
        const res = await fetch("/api/status");
        const data = await res.json();
        renderStatus(data);
      } catch (err) {
        showBanner("bad", `状态刷新失败：${err.message}`);
      }
    }

    async function loadDefaults() {
      try {
        const res = await fetch("/api/defaults");
        const data = await res.json();
        if (res.ok && data.ok) {
          fillDefaults(data.defaults);
        }
      } catch (err) {
        showBanner("warn", `加载默认配置失败：${err.message}`);
      }
    }

    async function saveConfig() {
      clearBanner();
      try {
        await postJson("/api/save-config", payloadFromForm());
        showBanner("ok", "配置已保存到 .env");
      } catch (err) {
        showBanner("bad", err.message);
      }
      await refreshStatus();
    }

    async function startStrategy() {
      clearBanner();
      try {
        await postJson("/api/start", { ...payloadFromForm(), save_env: true });
        showBanner("ok", "已发送启动请求");
      } catch (err) {
        showBanner("bad", err.message);
      }
      await refreshStatus();
    }

    async function stopStrategy() {
      clearBanner();
      try {
        await postJson("/api/stop", {});
        showBanner("warn", "策略已停止");
      } catch (err) {
        showBanner("bad", err.message);
      }
      await refreshStatus();
    }

    el("save_btn").addEventListener("click", saveConfig);
    el("start_btn").addEventListener("click", startStrategy);
    el("stop_btn").addEventListener("click", stopStrategy);
    el("refresh_btn").addEventListener("click", refreshStatus);
    el("take_profit_enabled").addEventListener("change", setTpSlEnabledStates);
    el("stop_loss_enabled").addEventListener("change", setTpSlEnabledStates);

    (async function init() {
      await loadDefaults();
      await refreshStatus();
      setInterval(refreshStatus, 2000);
    })();
  </script>
</body>
</html>
"""


class WebUIRequestHandler(BaseHTTPRequestHandler):
    runner: WebStrategyRunner
    env_path: Path

    server_version = "PolymarketWebUI/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep server terminal output clean.
        return

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTTPStatus.OK, WEB_UI_HTML)
            return

        if parsed.path == "/api/defaults":
            self._send_json(HTTPStatus.OK, {"ok": True, "defaults": load_defaults_from_env()})
            return

        if parsed.path == "/api/status":
            snapshot = self.runner.snapshot()
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "snapshot": {
                        "running": snapshot.running,
                        "started_at": snapshot.started_at,
                        "coin": snapshot.coin,
                        "market_window_minutes": snapshot.market_window_minutes,
                        "ws_connected": snapshot.ws_connected,
                        "market_slug": snapshot.market_slug,
                        "market_question": snapshot.market_question,
                        "countdown": snapshot.countdown,
                        "position_count": snapshot.position_count,
                        "stats": snapshot.stats,
                        "positions": snapshot.positions,
                        "last_error": snapshot.last_error,
                        "logs": snapshot.logs,
                    },
                    "env_file": str(self.env_path),
                    "env_exists": self.env_path.exists(),
                },
            )
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        try:
            data = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON payload"})
            return

        if parsed.path == "/api/save-config":
            settings, errors = validate_and_normalize_payload(data)
            if errors:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "Validation failed", "details": errors},
                )
                return

            assert settings is not None
            env_map = build_env_map(settings)
            save_env_file(self.env_path, env_map)
            load_dotenv(self.env_path, override=True)

            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "message": f"Saved configuration to {self.env_path}",
                },
            )
            return

        if parsed.path == "/api/start":
            settings, errors = validate_and_normalize_payload(data)
            if errors:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "Validation failed", "details": errors},
                )
                return

            assert settings is not None

            if _to_bool(data.get("save_env", True), True):
                env_map = build_env_map(settings)
                save_env_file(self.env_path, env_map)
                load_dotenv(self.env_path, override=True)

            ok, msg = self.runner.start(settings)
            status = HTTPStatus.OK if ok else HTTPStatus.CONFLICT
            self._send_json(status, {"ok": ok, "message": msg, "error": "" if ok else msg})
            return

        if parsed.path == "/api/stop":
            ok, msg = self.runner.stop()
            status = HTTPStatus.OK if ok else HTTPStatus.CONFLICT
            self._send_json(status, {"ok": ok, "message": msg, "error": "" if ok else msg})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})


def run_server(host: str, port: int, env_path: Path) -> None:
    load_dotenv(env_path, override=False)

    runner = WebStrategyRunner(env_path=env_path)
    WebUIRequestHandler.runner = runner
    WebUIRequestHandler.env_path = env_path

    server = ThreadingHTTPServer((host, port), WebUIRequestHandler)
    print(f"Web UI listening at http://{host}:{port}")
    print(f"Using env file: {env_path}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Web UI...")
    finally:
        try:
            runner.stop(timeout=2.0)
        except Exception:
            pass
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Polymarket Trading Bot Web UI")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host to bind (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to bind (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help=f"Path to .env file for loading/saving (default: {DEFAULT_ENV_FILE})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env_path = Path(args.env_file)
    run_server(args.host, args.port, env_path)


if __name__ == "__main__":
    main()
