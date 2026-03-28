"""
Unit tests for GammaClient market window helpers.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gamma_client import GammaClient


def test_get_slug_prefix_default_and_custom_interval():
    assert GammaClient.get_slug_prefix("eth") == "eth-updown-15m"
    assert GammaClient.get_slug_prefix("BTC", interval_minutes=5) == "btc-updown-5m"


def test_get_slug_prefix_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        GammaClient.get_slug_prefix("DOGE")

    with pytest.raises(ValueError):
        GammaClient.get_slug_prefix("ETH", interval_minutes=0)


def test_get_window_start_rounds_to_interval():
    now = datetime(2026, 1, 1, 12, 7, 45, tzinfo=timezone.utc)
    start = GammaClient.get_window_start(now=now, interval_minutes=5)

    assert start == datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc)


def test_get_current_market_checks_windows_in_order(monkeypatch):
    client = GammaClient(host="https://example.com")
    current_window = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    current_ts = int(current_window.timestamp())

    monkeypatch.setattr(
        client,
        "get_window_start",
        lambda now=None, interval_minutes=5: current_window,
    )

    calls = []

    def fake_get_market_by_slug(slug):
        calls.append(slug)
        if slug.endswith(str(current_ts + 300)):
            return {"slug": slug, "acceptingOrders": True}
        return None

    monkeypatch.setattr(client, "get_market_by_slug", fake_get_market_by_slug)

    market = client.get_current_market("ETH", interval_minutes=5)

    assert market is not None
    assert market["slug"].endswith(str(current_ts + 300))
    assert calls == [
        f"eth-updown-5m-{current_ts}",
        f"eth-updown-5m-{current_ts + 300}",
    ]


def test_get_current_market_fallback_uses_latest_active_market(monkeypatch):
    client = GammaClient(host="https://example.com")
    current_window = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    current_ts = int(current_window.timestamp())

    monkeypatch.setattr(
        client,
        "get_window_start",
        lambda now=None, interval_minutes=5: current_window,
    )
    monkeypatch.setattr(client, "get_market_by_slug", lambda slug: None)
    monkeypatch.setattr(
        client,
        "list_markets",
        lambda **kwargs: [
            {"slug": f"eth-updown-5m-{current_ts - 300}", "acceptingOrders": True},
            {"slug": f"eth-updown-5m-{current_ts + 600}", "acceptingOrders": True},
            {"slug": "other-market", "acceptingOrders": True},
        ],
    )

    market = client.get_current_market("ETH", interval_minutes=5)
    assert market is not None
    assert market["slug"].endswith(str(current_ts + 600))


def test_get_next_market_uses_interval(monkeypatch):
    client = GammaClient(host="https://example.com")
    current_window = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    current_ts = int(current_window.timestamp())

    monkeypatch.setattr(
        client,
        "get_window_start",
        lambda now=None, interval_minutes=5: current_window,
    )

    captured = {}

    def fake_get_market_by_slug(slug):
        captured["slug"] = slug
        return {"slug": slug}

    monkeypatch.setattr(client, "get_market_by_slug", fake_get_market_by_slug)

    market = client.get_next_market("ETH", interval_minutes=5)
    assert market == {"slug": f"eth-updown-5m-{current_ts + 300}"}
    assert captured["slug"] == f"eth-updown-5m-{current_ts + 300}"


def test_get_market_info_forwards_interval(monkeypatch):
    client = GammaClient(host="https://example.com")
    called = {}

    def fake_get_current_market(coin, interval_minutes=15):
        called["coin"] = coin
        called["interval"] = interval_minutes
        return {
            "slug": "eth-updown-5m-1",
            "question": "q",
            "endDate": "2026-01-01T00:05:00Z",
            "clobTokenIds": '["1", "2"]',
            "outcomes": '["Up", "Down"]',
            "outcomePrices": '["0.4", "0.6"]',
            "acceptingOrders": True,
        }

    monkeypatch.setattr(client, "get_current_market", fake_get_current_market)

    info = client.get_market_info("ETH", interval_minutes=5)
    assert info is not None
    assert called == {"coin": "ETH", "interval": 5}
    assert info["token_ids"] == {"up": "1", "down": "2"}
    assert info["prices"] == {"up": 0.4, "down": 0.6}
