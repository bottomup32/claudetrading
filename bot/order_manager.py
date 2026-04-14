"""
Order execution with fill management.
Handles limit order placement, fill monitoring, and retries.
"""
import logging
import time
from typing import Optional

from bot.alpaca_client import AlpacaClient
from bot.options_engine import calculate_limit_price
from bot.config import FILL_WAIT_SECONDS, MAX_FILL_ATTEMPTS, PLTR_WIDE_SPREAD_WAIT

logger = logging.getLogger(__name__)


def sell_option(
    client: AlpacaClient,
    contract: dict,
    ticker: str,
    qty: int = 1,
) -> Optional[dict]:
    """
    Sell an options contract (CSP or CC).
    Manages fill retries per ticker rules.
    Returns filled order info or None.
    """
    # PLTR: if spread is too wide at entry, wait
    if ticker == "PLTR" and contract["spread"] > 0.25:
        logger.info(f"PLTR spread ${contract['spread']:.2f} > $0.25 — waiting {PLTR_WIDE_SPREAD_WAIT}s for normalization")
        time.sleep(PLTR_WIDE_SPREAD_WAIT)
        # After wait, caller should re-fetch contract snapshot; here we proceed

    limit_price = calculate_limit_price(contract, ticker, toward_natural=0)
    order_id = client.place_limit_order(
        symbol=contract["symbol"],
        qty=qty,
        side="sell",
        limit_price=limit_price,
    )
    if not order_id:
        return None

    max_attempts = MAX_FILL_ATTEMPTS[ticker]
    wait_seconds = FILL_WAIT_SECONDS[ticker]

    for attempt in range(max_attempts + 1):
        time.sleep(wait_seconds)
        order = client.get_order(order_id)
        if order["status"] in ("filled", "partially_filled"):
            filled_price = order["filled_price"] or limit_price
            logger.info(f"Order {order_id} filled at ${filled_price:.2f} (attempt {attempt})")
            return {
                "order_id":     order_id,
                "filled_price": filled_price,
                "status":       order["status"],
            }
        if order["status"] in ("canceled", "expired", "rejected"):
            logger.warning(f"Order {order_id} ended with status {order['status']}")
            return None

        # Still open → adjust price
        if attempt < max_attempts:
            new_price = calculate_limit_price(contract, ticker, toward_natural=attempt + 1)
            logger.info(f"Adjusting order {order_id} to ${new_price:.2f} (attempt {attempt + 1})")
            new_id = client.replace_order_price(order_id, new_price)
            if new_id:
                order_id = new_id
            else:
                # Replace failed, cancel and give up
                client.cancel_order(order_id)
                return None

    # Max attempts exhausted
    client.cancel_order(order_id)
    logger.warning(f"Could not fill sell order for {contract['symbol']} after {max_attempts} attempts")
    return None


def buy_to_close(
    client: AlpacaClient,
    contract: dict,
    ticker: str,
    qty: int = 1,
) -> Optional[dict]:
    """
    Buy to close an existing short option position.
    Uses ask price as limit (we're buying, so be willing to pay ask).
    """
    # Pay at most ask, start at mid
    limit_price = round((contract["bid"] + contract["ask"]) / 2, 2)
    order_id = client.place_limit_order(
        symbol=contract["symbol"],
        qty=qty,
        side="buy",
        limit_price=limit_price,
    )
    if not order_id:
        return None

    max_attempts = MAX_FILL_ATTEMPTS[ticker]
    wait_seconds = FILL_WAIT_SECONDS[ticker]

    for attempt in range(max_attempts + 1):
        time.sleep(wait_seconds)
        order = client.get_order(order_id)
        if order["status"] in ("filled", "partially_filled"):
            filled_price = order["filled_price"] or limit_price
            logger.info(f"Buy-to-close {order_id} filled at ${filled_price:.2f}")
            return {
                "order_id":     order_id,
                "filled_price": filled_price,
                "status":       order["status"],
            }
        if order["status"] in ("canceled", "expired", "rejected"):
            return None

        if attempt < max_attempts:
            # Move toward ask to get filled
            new_price = min(contract["ask"], limit_price + 0.02 * (attempt + 1))
            new_id = client.replace_order_price(order_id, round(new_price, 2))
            if new_id:
                order_id = new_id
            else:
                client.cancel_order(order_id)
                return None

    client.cancel_order(order_id)
    logger.warning(f"Could not fill buy-to-close for {contract['symbol']}")
    return None


def get_current_option_snapshot(client: AlpacaClient, contract_symbol: str, ticker: str) -> Optional[dict]:
    """
    Fetch a live snapshot for an existing open contract (to compute current P&L).
    """
    try:
        chain = client.get_option_chain(
            underlying=ticker,
            option_type="put",   # will fetch both via underlying; filter below
            dte_min=0,
            dte_max=60,
            strike_pct_range=0.50,
        )
        for c in chain:
            if c["symbol"] == contract_symbol:
                return c
        # Try calls if not found in puts
        chain_calls = client.get_option_chain(
            underlying=ticker,
            option_type="call",
            dte_min=0,
            dte_max=60,
            strike_pct_range=0.50,
        )
        for c in chain_calls:
            if c["symbol"] == contract_symbol:
                return c
    except Exception as e:
        logger.warning(f"Snapshot fetch for {contract_symbol} failed: {e}")
    return None


def check_profit_pct(entry_premium: float, current_value: float) -> float:
    """
    For a short option:
      profit_pct = (entry_premium - current_value) / entry_premium
    current_value = current mid price of the contract.
    """
    if entry_premium <= 0:
        return 0.0
    return (entry_premium - current_value) / entry_premium
