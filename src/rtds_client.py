"""
RTDS WebSocket Client - Real-time Crypto Price Streaming

Provides a lightweight client for Polymarket's Real-Time Data Socket (RTDS)
crypto price feed so strategies can react to live spot moves.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, List, Optional, Set, Union

from .websocket_client import _load_websockets

if TYPE_CHECKING:
    from websockets.client import WebSocketClientProtocol

logger = logging.getLogger(__name__)

RTDS_URL = "wss://ws-live-data.polymarket.com"


@dataclass
class CryptoPriceUpdate:
    """A single RTDS crypto price update."""

    symbol: str
    price: float
    timestamp: int
    topic: str = "crypto_prices"

    @classmethod
    def from_message(cls, msg: Dict[str, object]) -> "CryptoPriceUpdate":
        """Create an update object from an RTDS message."""
        payload = msg.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        timestamp = payload.get("timestamp", msg.get("timestamp", 0))
        return cls(
            symbol=str(payload.get("symbol", "")).lower(),
            price=float(payload.get("value", 0)),
            timestamp=int(timestamp),
            topic=str(msg.get("topic", "crypto_prices")),
        )


PriceCallback = Callable[[CryptoPriceUpdate], Union[None, Awaitable[None]]]
ErrorCallback = Callable[[Exception], None]


class CryptoPriceWebSocket:
    """
    WebSocket client for RTDS crypto price updates.

    Example:
        ws = CryptoPriceWebSocket()

        @ws.on_price
        async def handle_price(update: CryptoPriceUpdate):
            print(update.symbol, update.price)

        await ws.subscribe_crypto_prices(["btcusdt"])
        await ws.run()
    """

    def __init__(
        self,
        url: str = RTDS_URL,
        reconnect_interval: float = 5.0,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
    ):
        self.url = url
        self.reconnect_interval = reconnect_interval
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        self._ws_connect, self._connection_closed = _load_websockets()

        self._ws: Optional["WebSocketClientProtocol"] = None
        self._running = False
        self._symbols: Set[str] = set()
        self._prices: Dict[str, CryptoPriceUpdate] = {}

        self._on_price: Optional[PriceCallback] = None
        self._on_error: Optional[ErrorCallback] = None
        self._on_connect: Optional[Callable[[], None]] = None
        self._on_disconnect: Optional[Callable[[], None]] = None

    @property
    def is_connected(self) -> bool:
        """Check if the socket is currently connected."""
        if self._ws is None:
            return False
        try:
            from websockets.protocol import State
            return self._ws.state == State.OPEN
        except (ImportError, AttributeError):
            try:
                return self._ws.open
            except AttributeError:
                return False

    def on_price(self, callback: PriceCallback) -> PriceCallback:
        """Register a callback for price updates."""
        self._on_price = callback
        return callback

    def on_error(self, callback: ErrorCallback) -> ErrorCallback:
        """Register a callback for errors."""
        self._on_error = callback
        return callback

    def on_connect(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for successful connections."""
        self._on_connect = callback
        return callback

    def on_disconnect(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for disconnects."""
        self._on_disconnect = callback
        return callback

    def get_price(self, symbol: str) -> float:
        """Get the latest known price for a symbol."""
        update = self._prices.get(symbol.lower())
        return update.price if update else 0.0

    def get_update(self, symbol: str) -> Optional[CryptoPriceUpdate]:
        """Get the latest cached update for a symbol."""
        return self._prices.get(symbol.lower())

    async def connect(self) -> bool:
        """Open the RTDS connection."""
        if not self._ws_connect:
            logger.error("websockets package not installed")
            return False

        try:
            self._ws = await self._ws_connect(
                self.url,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
            )
            if self._on_connect:
                self._on_connect()
            return True
        except Exception as exc:
            logger.error("Failed to connect to RTDS: %s", exc)
            if self._on_error:
                self._on_error(exc)
            return False

    async def disconnect(self) -> None:
        """Close the RTDS connection."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            finally:
                self._ws = None

    def _build_subscribe_message(self) -> Dict[str, object]:
        """Build the RTDS subscribe payload for the tracked symbols."""
        filters = ",".join(sorted(self._symbols))
        subscription: Dict[str, object] = {
            "topic": "crypto_prices",
            "type": "update",
        }
        if filters:
            subscription["filters"] = filters

        return {
            "action": "subscribe",
            "subscriptions": [subscription],
        }

    async def _send_subscription(self) -> bool:
        """Send the current crypto price subscription."""
        if not self.is_connected or self._ws is None:
            return False

        try:
            await self._ws.send(json.dumps(self._build_subscribe_message()))
            return True
        except Exception as exc:
            logger.error("Failed to subscribe to RTDS prices: %s", exc)
            if self._on_error:
                self._on_error(exc)
            return False

    async def subscribe_crypto_prices(
        self,
        symbols: List[str],
        replace: bool = False,
    ) -> bool:
        """
        Subscribe to one or more RTDS crypto price symbols.

        Args:
            symbols: Symbols such as ["btcusdt"]
            replace: When True, replace the current tracked symbol set
        """
        normalized = {
            symbol.strip().lower()
            for symbol in symbols
            if symbol and symbol.strip()
        }
        if replace:
            self._symbols.clear()
        self._symbols.update(normalized)

        if not self.is_connected:
            return True

        return await self._send_subscription()

    async def _handle_message(self, data: Dict[str, object]) -> None:
        """Handle a decoded RTDS message."""
        topic = str(data.get("topic", ""))
        if topic != "crypto_prices":
            return

        try:
            update = CryptoPriceUpdate.from_message(data)
        except (TypeError, ValueError) as exc:
            logger.error("Failed to parse RTDS price message: %s", exc)
            if self._on_error:
                self._on_error(exc)
            return

        if update.price <= 0 or not update.symbol:
            return

        self._prices[update.symbol] = update

        if self._on_price:
            result = self._on_price(update)
            if asyncio.iscoroutine(result):
                await result

    async def _run_loop(self) -> None:
        """Read messages until the socket closes or the client stops."""
        while self._running and self.is_connected and self._ws is not None:
            try:
                message = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=self.ping_interval + 5,
                )
                data = json.loads(message)
                if isinstance(data, dict):
                    await self._handle_message(data)
            except asyncio.TimeoutError:
                logger.warning("RTDS receive timeout")
            except self._connection_closed as exc:
                logger.warning("RTDS connection closed: %s", exc)
                break
            except json.JSONDecodeError as exc:
                logger.error("Failed to decode RTDS message: %s", exc)
            except Exception as exc:
                logger.error("Error processing RTDS message: %s", exc)
                if self._on_error:
                    self._on_error(exc)

    async def run(self, auto_reconnect: bool = True) -> None:
        """Run the client until stopped or cancelled."""
        self._running = True

        while self._running:
            if not await self.connect():
                if not auto_reconnect:
                    break
                await asyncio.sleep(self.reconnect_interval)
                continue

            if self._symbols:
                await self._send_subscription()

            await self._run_loop()

            if self._on_disconnect:
                self._on_disconnect()

            if not self._running:
                break

            if auto_reconnect:
                await asyncio.sleep(self.reconnect_interval)
            else:
                break

    async def run_until_cancelled(self) -> None:
        """Run until cancelled, closing the socket on exit."""
        try:
            await self.run(auto_reconnect=True)
        except asyncio.CancelledError:
            await self.disconnect()

    def stop(self) -> None:
        """Stop reconnect attempts and exit the run loop."""
        self._running = False
