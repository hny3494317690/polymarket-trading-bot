"""
Unit tests for FlashCrash strategy entry filters.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.flash_crash import FlashCrashConfig, FlashCrashStrategy


class DummyBot:
    pass


def test_parse_change_ranges_bps():
    parsed = FlashCrashStrategy.parse_change_ranges_bps("40-999 -999--40")
    assert parsed == [(40, 999), (-999, -40)]


def test_parse_change_ranges_bps_invalid():
    with pytest.raises(ValueError):
        FlashCrashStrategy.parse_change_ranges_bps("40to999")


def test_open_change_range_filter():
    strategy = FlashCrashStrategy(
        bot=DummyBot(),
        config=FlashCrashConfig(open_change_ranges_bps="40-999 -999--40"),
    )
    strategy._opening_prices["up"] = 0.5

    # +200 bps
    assert strategy._in_open_change_ranges("up", 0.51) is True
    # +20 bps (outside)
    assert strategy._in_open_change_ranges("up", 0.501) is False
    # -200 bps
    assert strategy._in_open_change_ranges("up", 0.49) is True


def test_yes_no_price_ranges():
    strategy = FlashCrashStrategy(
        bot=DummyBot(),
        config=FlashCrashConfig(
            yes_price_min=0.7,
            yes_price_max=0.9,
            no_price_min=0.1,
            no_price_max=0.3,
        ),
    )

    assert strategy._in_side_price_range("up", 0.8) is True
    assert strategy._in_side_price_range("up", 0.69) is False
    assert strategy._in_side_price_range("down", 0.2) is True
    assert strategy._in_side_price_range("down", 0.31) is False
