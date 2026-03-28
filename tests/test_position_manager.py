"""
Unit tests for Position and PositionManager TP/SL behavior.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.position_manager import Position, PositionManager


def test_position_take_profit_disabled():
    position = Position(
        id="p1",
        side="up",
        token_id="t1",
        entry_price=0.40,
        size=10.0,
        entry_time=time.time(),
        take_profit_delta=None,
        stop_loss_delta=0.05,
    )

    assert position.take_profit_price is None
    assert position.check_take_profit(0.90) is False


def test_position_stop_loss_disabled():
    position = Position(
        id="p1",
        side="up",
        token_id="t1",
        entry_price=0.40,
        size=10.0,
        entry_time=time.time(),
        take_profit_delta=0.10,
        stop_loss_delta=None,
    )

    assert position.stop_loss_price is None
    assert position.check_stop_loss(0.01) is False


def test_position_non_positive_levels_are_treated_as_disabled():
    position = Position(
        id="p1",
        side="up",
        token_id="t1",
        entry_price=0.40,
        size=10.0,
        entry_time=time.time(),
        take_profit_delta=0.0,
        stop_loss_delta=-0.05,
    )

    assert position.take_profit_price is None
    assert position.stop_loss_price is None


def test_position_manager_open_position_uses_optional_levels():
    manager = PositionManager(take_profit=None, stop_loss=None)
    position = manager.open_position("up", "t1", entry_price=0.40, size=10.0)

    assert position is not None
    assert position.take_profit_delta is None
    assert position.stop_loss_delta is None


def test_check_exit_returns_none_when_tp_sl_disabled():
    manager = PositionManager(take_profit=None, stop_loss=None)
    position = manager.open_position("up", "t1", entry_price=0.40, size=10.0)
    assert position is not None

    exit_type, pnl = manager.check_exit(position.id, current_price=0.90)
    assert exit_type is None
    assert pnl == (0.90 - 0.40) * 10.0
