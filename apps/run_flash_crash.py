#!/usr/bin/env python3
"""
Flash Crash Strategy Runner

Entry point for running the flash crash strategy.

Usage:
    python apps/run_flash_crash.py --coin ETH
    python apps/run_flash_crash.py --coin BTC --size 10
    python apps/run_flash_crash.py --coin BTC --drop 0.25
    python apps/run_flash_crash.py --coin BTC --market-window 5
    python apps/run_flash_crash.py --no-take-profit --no-stop-loss
"""

import os
import sys
import asyncio
import argparse
import logging
from pathlib import Path

# Suppress noisy logs
logging.getLogger("src.websocket_client").setLevel(logging.WARNING)
logging.getLogger("src.bot").setLevel(logging.WARNING)

# Auto-load .env file
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.console import Colors
from src.bot import TradingBot
from src.config import Config
from strategies.flash_crash import FlashCrashStrategy, FlashCrashConfig


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Flash Crash Strategy for Polymarket short-duration markets"
    )
    parser.add_argument(
        "--coin",
        type=str,
        default="ETH",
        choices=["BTC", "ETH", "SOL", "XRP"],
        help="Coin to trade (default: ETH)"
    )
    parser.add_argument(
        "--size",
        type=float,
        default=5.0,
        help="Trade size in USDC (default: 5.0)"
    )
    parser.add_argument(
        "--market-window",
        type=int,
        default=15,
        help="Market window size in minutes (default: 15)"
    )
    parser.add_argument(
        "--drop",
        type=float,
        default=0.30,
        help="Flash-crash detection threshold as absolute probability drop (default: 0.30)"
    )
    parser.add_argument(
        "--open-change-ranges",
        type=str,
        default="",
        help="Opening-change ranges in bps, e.g. '40-999 -999--40' (optional)"
    )
    parser.add_argument(
        "--yes-min-price",
        type=float,
        default=None,
        help="YES (UP) min price filter (optional)"
    )
    parser.add_argument(
        "--yes-max-price",
        type=float,
        default=None,
        help="YES (UP) max price filter (optional)"
    )
    parser.add_argument(
        "--no-min-price",
        type=float,
        default=None,
        help="NO (DOWN) min price filter (optional)"
    )
    parser.add_argument(
        "--no-max-price",
        type=float,
        default=None,
        help="NO (DOWN) max price filter (optional)"
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=10,
        help="Lookback window in seconds (default: 10)"
    )
    parser.add_argument(
        "--take-profit",
        type=float,
        default=0.10,
        help="Take profit in dollars (default: 0.10)"
    )
    parser.add_argument(
        "--stop-loss",
        type=float,
        default=0.05,
        help="Stop loss in dollars (default: 0.05)"
    )
    parser.add_argument(
        "--no-take-profit",
        action="store_true",
        help="Disable take profit auto-exit"
    )
    parser.add_argument(
        "--no-stop-loss",
        action="store_true",
        help="Disable stop loss auto-exit"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.market_window <= 0:
        parser.error("--market-window must be greater than 0")
    if args.take_profit < 0:
        parser.error("--take-profit must be >= 0")
    if args.stop_loss < 0:
        parser.error("--stop-loss must be >= 0")
    for name, val in [
        ("--yes-min-price", args.yes_min_price),
        ("--yes-max-price", args.yes_max_price),
        ("--no-min-price", args.no_min_price),
        ("--no-max-price", args.no_max_price),
    ]:
        if val is not None and not (0 <= val <= 1):
            parser.error(f"{name} must be in [0, 1]")
    if args.yes_min_price is not None and args.yes_max_price is not None and args.yes_min_price > args.yes_max_price:
        parser.error("--yes-min-price cannot be greater than --yes-max-price")
    if args.no_min_price is not None and args.no_max_price is not None and args.no_min_price > args.no_max_price:
        parser.error("--no-min-price cannot be greater than --no-max-price")

    # Enable debug logging if requested
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        logging.getLogger("src.websocket_client").setLevel(logging.DEBUG)

    # Check environment
    private_key = os.environ.get("POLY_PRIVATE_KEY")
    safe_address = os.environ.get("POLY_SAFE_ADDRESS")

    if not private_key or not safe_address:
        print(f"{Colors.RED}Error: POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS must be set{Colors.RESET}")
        print("Set them in .env file or export as environment variables")
        sys.exit(1)

    # Create bot
    config = Config.from_env()
    bot = TradingBot(config=config, private_key=private_key)

    if not bot.is_initialized():
        print(f"{Colors.RED}Error: Failed to initialize bot{Colors.RESET}")
        sys.exit(1)

    take_profit = None if args.no_take_profit or args.take_profit == 0 else args.take_profit
    stop_loss = None if args.no_stop_loss or args.stop_loss == 0 else args.stop_loss

    # Create strategy config
    strategy_config = FlashCrashConfig(
        coin=args.coin.upper(),
        size=args.size,
        drop_threshold=args.drop,
        price_lookback_seconds=args.lookback,
        take_profit=take_profit,
        stop_loss=stop_loss,
        market_window_minutes=args.market_window,
        open_change_ranges_bps=args.open_change_ranges,
        yes_price_min=args.yes_min_price,
        yes_price_max=args.yes_max_price,
        no_price_min=args.no_min_price,
        no_price_max=args.no_max_price,
    )

    # Print configuration
    print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(
        f"{Colors.BOLD}  Flash Crash Strategy - {strategy_config.coin} "
        f"{strategy_config.market_window_minutes}-Minute Markets{Colors.RESET}"
    )
    print(f"{Colors.BOLD}{'='*60}{Colors.RESET}\n")

    print(f"Configuration:")
    print(f"  Coin: {strategy_config.coin}")
    print(f"  Market window: {strategy_config.market_window_minutes}m")
    print(f"  Size: ${strategy_config.size:.2f}")
    print(f"  Drop threshold: {strategy_config.drop_threshold:.2f}")
    print(f"  Open-change ranges (bps): {strategy_config.open_change_ranges_bps or 'any'}")
    yes_range = (
        f"{strategy_config.yes_price_min if strategy_config.yes_price_min is not None else '-'}"
        f"~{strategy_config.yes_price_max if strategy_config.yes_price_max is not None else '-'}"
    )
    no_range = (
        f"{strategy_config.no_price_min if strategy_config.no_price_min is not None else '-'}"
        f"~{strategy_config.no_price_max if strategy_config.no_price_max is not None else '-'}"
    )
    print(f"  YES price range: {yes_range}")
    print(f"  NO price range: {no_range}")
    print(f"  Lookback: {strategy_config.price_lookback_seconds}s")
    if strategy_config.take_profit is None:
        print("  Take profit: OFF")
    else:
        print(f"  Take profit: +${strategy_config.take_profit:.2f}")
    if strategy_config.stop_loss is None:
        print("  Stop loss: OFF")
    else:
        print(f"  Stop loss: -${strategy_config.stop_loss:.2f}")
    print()

    # Create and run strategy
    strategy = FlashCrashStrategy(bot=bot, config=strategy_config)

    try:
        asyncio.run(strategy.run())
    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        print(f"\n{Colors.RED}Error: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
