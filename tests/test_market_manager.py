"""
Unit tests for MarketManager market switching logic.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.market_manager import MarketManager, MarketInfo


def _market(slug: str, token_ids: dict, end_date: str = "") -> MarketInfo:
    return MarketInfo(
        slug=slug,
        question="",
        end_date=end_date,
        token_ids=token_ids,
        prices={},
        accepting_orders=True,
    )


def test_should_switch_market_newer_slug():
    manager = MarketManager(coin="BTC")
    old_market = _market("btc-updown-15m-1000", {"up": "1", "down": "2"})
    new_market = _market("btc-updown-15m-1100", {"up": "3", "down": "4"})

    assert manager._should_switch_market(old_market, new_market) is True


def test_should_not_switch_market_older_slug():
    manager = MarketManager(coin="BTC")
    old_market = _market("btc-updown-15m-1100", {"up": "1", "down": "2"})
    new_market = _market("btc-updown-15m-1000", {"up": "3", "down": "4"})

    assert manager._should_switch_market(old_market, new_market) is False


def test_should_not_switch_when_tokens_same():
    manager = MarketManager(coin="BTC")
    old_market = _market("btc-updown-15m-1000", {"up": "1", "down": "2"})
    new_market = _market("btc-updown-15m-1100", {"up": "1", "down": "2"})

    assert manager._should_switch_market(old_market, new_market) is False


def test_should_switch_with_end_date_fallback():
    manager = MarketManager(coin="BTC")
    old_market = _market(
        "btc-updown-15m-old",
        {"up": "1", "down": "2"},
        end_date="2025-01-01T00:15:00Z",
    )
    new_market = _market(
        "btc-updown-15m-new",
        {"up": "3", "down": "4"},
        end_date="2025-01-01T00:30:00Z",
    )

    assert manager._should_switch_market(old_market, new_market) is True


def test_discover_market_uses_configured_window(monkeypatch):
    manager = MarketManager(coin="BTC", market_window_minutes=5)
    called = {}

    def fake_get_market_info(coin, interval_minutes=15):
        called["coin"] = coin
        called["interval_minutes"] = interval_minutes
        return {
            "slug": "btc-updown-5m-1000",
            "question": "q",
            "end_date": "2025-01-01T00:05:00Z",
            "token_ids": {"up": "1", "down": "2"},
            "prices": {"up": 0.5, "down": 0.5},
            "accepting_orders": True,
        }

    monkeypatch.setattr(manager.gamma, "get_market_info", fake_get_market_info)

    market = manager.discover_market(update_state=False)
    assert market is not None
    assert market.slug == "btc-updown-5m-1000"
    assert called == {"coin": "BTC", "interval_minutes": 5}
