"""
Unit tests for RTDS crypto price client.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rtds_client import CryptoPriceUpdate, CryptoPriceWebSocket


class DummySocket:
    def __init__(self):
        self.open = True
        self.sent_messages = []

    async def send(self, message):
        self.sent_messages.append(message)


def test_crypto_price_update_from_message():
    msg = {
        "topic": "crypto_prices",
        "payload": {
            "symbol": "ETHUSDT",
            "value": "2012.5",
            "timestamp": 123456,
        },
    }

    update = CryptoPriceUpdate.from_message(msg)
    assert update.symbol == "ethusdt"
    assert update.price == 2012.5
    assert update.timestamp == 123456


def test_build_subscribe_message_sorts_filters():
    ws = CryptoPriceWebSocket()
    ws._symbols = {"btcusdt", "ethusdt"}

    payload = ws._build_subscribe_message()
    assert payload["action"] == "subscribe"
    assert payload["subscriptions"][0]["topic"] == "crypto_prices"
    assert payload["subscriptions"][0]["filters"] == "btcusdt,ethusdt"


@pytest.mark.asyncio
async def test_subscribe_crypto_prices_replace():
    ws = CryptoPriceWebSocket()
    await ws.subscribe_crypto_prices(["ethusdt"])
    await ws.subscribe_crypto_prices(["btcusdt"], replace=True)

    assert ws._symbols == {"btcusdt"}


@pytest.mark.asyncio
async def test_subscribe_crypto_prices_sends_when_connected():
    ws = CryptoPriceWebSocket()
    ws._ws = DummySocket()

    ok = await ws.subscribe_crypto_prices(["ethusdt"])

    assert ok is True
    assert ws._ws.sent_messages


@pytest.mark.asyncio
async def test_handle_message_updates_cache_and_calls_callback():
    ws = CryptoPriceWebSocket()
    called = {}

    @ws.on_price
    async def on_price(update):
        called["symbol"] = update.symbol
        called["price"] = update.price

    await ws._handle_message(
        {
            "topic": "crypto_prices",
            "payload": {
                "symbol": "BTCUSDT",
                "value": "99999.1",
                "timestamp": 11,
            },
        }
    )

    assert ws.get_price("btcusdt") == 99999.1
    assert called == {"symbol": "btcusdt", "price": 99999.1}
