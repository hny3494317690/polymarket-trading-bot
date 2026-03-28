"""
Gamma API Client - Market Discovery for Polymarket

Provides access to the Gamma API for discovering active markets,
including 15-minute Up/Down markets for crypto assets.

Example:
    from src.gamma_client import GammaClient

    client = GammaClient()
    market = client.get_current_15m_market("ETH")
    print(market["slug"], market["clobTokenIds"])
"""

import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

from .http import ThreadLocalSessionMixin


class GammaClient(ThreadLocalSessionMixin):
    """
    Client for Polymarket's Gamma API.

    Used to discover markets and get market metadata.
    """

    DEFAULT_HOST = "https://gamma-api.polymarket.com"

    # Supported coins and their slug prefixes
    COIN_PREFIXES = {
        "BTC": "btc",
        "ETH": "eth",
        "SOL": "sol",
        "XRP": "xrp",
    }

    def __init__(self, host: str = DEFAULT_HOST, timeout: int = 10):
        """
        Initialize Gamma client.

        Args:
            host: Gamma API host URL
            timeout: Request timeout in seconds
        """
        super().__init__()
        self.host = host.rstrip("/")
        self.timeout = timeout

    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Get market data by slug.

        Args:
            slug: Market slug (e.g., "eth-updown-15m-1766671200")

        Returns:
            Market data dictionary or None if not found
        """
        url = f"{self.host}/markets/slug/{slug}"

        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    def list_markets(self, **params: Any) -> List[Dict[str, Any]]:
        """
        List markets from the Gamma API.

        Args:
            **params: Query parameters forwarded to /markets

        Returns:
            List of market dictionaries
        """
        url = f"{self.host}/markets"

        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code != 200:
                return []
            data = response.json()
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @classmethod
    def get_slug_prefix(cls, coin: str, interval_minutes: int = 15) -> str:
        """
        Build the slug prefix for a coin/window pair.

        Args:
            coin: Coin symbol (BTC, ETH, SOL, XRP)
            interval_minutes: Market window size in minutes

        Returns:
            Slug prefix, e.g. "btc-updown-5m"
        """
        coin = coin.upper()
        if coin not in cls.COIN_PREFIXES:
            raise ValueError(
                f"Unsupported coin: {coin}. Use: {list(cls.COIN_PREFIXES.keys())}"
            )
        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be greater than 0")

        return f"{cls.COIN_PREFIXES[coin]}-updown-{interval_minutes}m"

    @staticmethod
    def get_window_start(
        now: Optional[datetime] = None,
        interval_minutes: int = 15
    ) -> datetime:
        """
        Get the UTC start time of the current market window.

        Args:
            now: Current timestamp (defaults to current UTC time)
            interval_minutes: Market window size in minutes

        Returns:
            Window start as a timezone-aware UTC datetime
        """
        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be greater than 0")

        current = now or datetime.now(timezone.utc)
        window_seconds = interval_minutes * 60
        current_ts = int(current.timestamp())
        start_ts = current_ts - (current_ts % window_seconds)
        return datetime.fromtimestamp(start_ts, tz=timezone.utc)

    @staticmethod
    def _market_sort_key(market: Dict[str, Any]) -> tuple[int, int]:
        """Get a stable ordering key for candidate markets."""
        slug = str(market.get("slug", ""))
        slug_ts = 0
        suffix = slug.split("-")[-1] if slug else ""
        if suffix.isdigit():
            slug_ts = int(suffix)

        end_ts = 0
        end_date = market.get("endDate")
        if end_date:
            try:
                end_ts = int(datetime.fromisoformat(str(end_date).replace("Z", "+00:00")).timestamp())
            except Exception:
                end_ts = 0

        return (slug_ts, end_ts)

    def _find_active_market_by_prefix(
        self,
        prefix: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Fallback lookup for active markets when direct slug discovery misses.

        Args:
            prefix: Prefix such as "btc-updown-5m"

        Returns:
            Best matching active market, if any
        """
        markets = self.list_markets(limit=200, closed=False)
        matches = [
            market for market in markets
            if str(market.get("slug", "")).startswith(f"{prefix}-")
            and market.get("acceptingOrders")
        ]

        if not matches:
            return None

        # Pick the most recent active market when multiple matches exist.
        matches.sort(key=self._market_sort_key)
        return matches[-1]

    def get_current_market(
        self,
        coin: str,
        interval_minutes: int = 15
    ) -> Optional[Dict[str, Any]]:
        """
        Get the current active market for a coin/window pair.

        Args:
            coin: Coin symbol (BTC, ETH, SOL, XRP)
            interval_minutes: Market window size in minutes

        Returns:
            Market data for the current window, or None
        """
        prefix = self.get_slug_prefix(coin, interval_minutes)
        current_window = self.get_window_start(interval_minutes=interval_minutes)
        current_ts = int(current_window.timestamp())
        window_seconds = interval_minutes * 60

        # Try current window
        slug = f"{prefix}-{current_ts}"
        market = self.get_market_by_slug(slug)

        if market and market.get("acceptingOrders"):
            return market

        # Try next window (in case current just ended)
        next_ts = current_ts + window_seconds
        slug = f"{prefix}-{next_ts}"
        market = self.get_market_by_slug(slug)

        if market and market.get("acceptingOrders"):
            return market

        # Try previous window (might still be active)
        prev_ts = current_ts - window_seconds
        slug = f"{prefix}-{prev_ts}"
        market = self.get_market_by_slug(slug)

        if market and market.get("acceptingOrders"):
            return market

        return self._find_active_market_by_prefix(prefix)

    def get_current_15m_market(self, coin: str) -> Optional[Dict[str, Any]]:
        """Backward-compatible helper for the 15-minute market."""
        return self.get_current_market(coin, interval_minutes=15)

    def get_next_market(
        self,
        coin: str,
        interval_minutes: int = 15
    ) -> Optional[Dict[str, Any]]:
        """
        Get the next upcoming market for a coin/window pair.

        Args:
            coin: Coin symbol (BTC, ETH, SOL, XRP)
            interval_minutes: Market window size in minutes

        Returns:
            Market data for the next window, or None
        """
        prefix = self.get_slug_prefix(coin, interval_minutes)
        current_window = self.get_window_start(interval_minutes=interval_minutes)
        next_window = datetime.fromtimestamp(
            int(current_window.timestamp()) + (interval_minutes * 60),
            tz=timezone.utc
        )
        next_ts = int(next_window.timestamp())
        slug = f"{prefix}-{next_ts}"

        return self.get_market_by_slug(slug)

    def get_next_15m_market(self, coin: str) -> Optional[Dict[str, Any]]:
        """Backward-compatible helper for the next 15-minute market."""
        return self.get_next_market(coin, interval_minutes=15)

    def parse_token_ids(self, market: Dict[str, Any]) -> Dict[str, str]:
        """
        Parse token IDs from market data.

        Args:
            market: Market data dictionary

        Returns:
            Dictionary with "up" and "down" token IDs
        """
        clob_token_ids = market.get("clobTokenIds", "[]")
        token_ids = self._parse_json_field(clob_token_ids)

        outcomes = market.get("outcomes", '["Up", "Down"]')
        outcomes = self._parse_json_field(outcomes)

        return self._map_outcomes(outcomes, token_ids)

    def parse_prices(self, market: Dict[str, Any]) -> Dict[str, float]:
        """
        Parse current prices from market data.

        Args:
            market: Market data dictionary

        Returns:
            Dictionary with "up" and "down" prices
        """
        outcome_prices = market.get("outcomePrices", '["0.5", "0.5"]')
        prices = self._parse_json_field(outcome_prices)

        outcomes = market.get("outcomes", '["Up", "Down"]')
        outcomes = self._parse_json_field(outcomes)

        return self._map_outcomes(outcomes, prices, cast=float)

    @staticmethod
    def _parse_json_field(value: Any) -> List[Any]:
        """Parse a field that may be a JSON string or a list."""
        if isinstance(value, str):
            return json.loads(value)
        return value

    @staticmethod
    def _map_outcomes(
        outcomes: List[Any],
        values: List[Any],
        cast=lambda v: v
    ) -> Dict[str, Any]:
        """Map outcome labels to values with optional casting."""
        result: Dict[str, Any] = {}
        for i, outcome in enumerate(outcomes):
            if i < len(values):
                result[str(outcome).lower()] = cast(values[i])
        return result

    def get_market_info(
        self,
        coin: str,
        interval_minutes: int = 15
    ) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive market info for the current market.

        Args:
            coin: Coin symbol
            interval_minutes: Market window size in minutes

        Returns:
            Dictionary with market info including token IDs and prices
        """
        market = self.get_current_market(coin, interval_minutes=interval_minutes)
        if not market:
            return None

        token_ids = self.parse_token_ids(market)
        prices = self.parse_prices(market)

        return {
            "slug": market.get("slug"),
            "question": market.get("question"),
            "end_date": market.get("endDate"),
            "token_ids": token_ids,
            "prices": prices,
            "accepting_orders": market.get("acceptingOrders", False),
            "best_bid": market.get("bestBid"),
            "best_ask": market.get("bestAsk"),
            "spread": market.get("spread"),
            "raw": market,
        }
