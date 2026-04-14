"""
Options contract selection logic.
Given market conditions, find the best put (CSP) or call (CC) to sell.
"""
import logging
from typing import Optional

from bot.config import (
    CSP_DELTA, CC_DELTA, CSP_MAX_DTE, CC_MAX_DTE,
    MIN_ANNUALIZED_RETURN, MAX_SPREAD,
    iv_rank_bucket,
)

logger = logging.getLogger(__name__)


def compute_delta_target(ticker: str, iv_rank: float, vix_zone: str, stage: str) -> tuple[float, float]:
    """
    Returns (delta_min, delta_max) absolute values after applying VIX shift.
    stage = "CSP" or "CC"
    """
    from bot.config import VIX_ZONE_PARAMS
    bucket = iv_rank_bucket(iv_rank)
    delta_table = CSP_DELTA if stage == "CSP" else CC_DELTA
    base_min, base_max = delta_table[ticker][bucket]

    _, _, _, vix_shift = VIX_ZONE_PARAMS[vix_zone]

    # For CSP: shift further OTM = reduce delta magnitude
    # For CC:  shift further OTM = reduce delta magnitude
    adj_min = max(0.05, base_min - vix_shift)
    adj_max = max(0.05, base_max - vix_shift)
    return (adj_min, adj_max)


def check_premium_threshold(ticker: str, premium: float, strike: float, dte: int) -> bool:
    """Annualized return on capital ≥ minimum threshold."""
    if dte <= 0 or strike <= 0:
        return False
    ann_return = (premium / (strike * 100)) * (365 / dte)
    threshold  = MIN_ANNUALIZED_RETURN[ticker]
    ok = ann_return >= threshold
    if not ok:
        logger.debug(
            f"{ticker} premium check FAIL: ann_return={ann_return:.1%} < threshold={threshold:.1%}"
        )
    return ok


def check_spread(ticker: str, bid: float, ask: float) -> bool:
    """Reject contracts with bid-ask spread above threshold."""
    spread = ask - bid
    limit  = MAX_SPREAD[ticker]
    ok = spread <= limit
    if not ok:
        logger.debug(f"{ticker} spread FAIL: ${spread:.2f} > limit ${limit:.2f}")
    return ok


def find_best_put(
    contracts: list[dict],
    ticker: str,
    iv_rank: float,
    vix_zone: str,
    max_dte: int,
) -> Optional[dict]:
    """
    From a list of put contract snapshots, return the best one matching:
    - Delta within target range (after VIX adjustment)
    - Premium ≥ annualized threshold
    - Spread ≤ maximum
    - DTE ≤ max_dte
    Picks the contract closest to the center of the delta range.
    """
    delta_min, delta_max = compute_delta_target(ticker, iv_rank, vix_zone, "CSP")
    logger.info(f"{ticker} CSP delta target: [{delta_min:.2f}, {delta_max:.2f}] | VIX zone: {vix_zone}")

    candidates = []
    for c in contracts:
        if c["type"] != "put":
            continue
        if c["dte"] > max_dte or c["dte"] < 2:
            continue

        # Delta for puts is negative; compare absolute value
        abs_delta = abs(c["delta"])
        if not (delta_min <= abs_delta <= delta_max):
            continue
        if not check_spread(ticker, c["bid"], c["ask"]):
            continue
        if not check_premium_threshold(ticker, c["mid"], c["strike"], c["dte"]):
            continue

        candidates.append(c)

    if not candidates:
        logger.warning(f"{ticker} CSP: no qualifying put found (delta [{delta_min:.2f},{delta_max:.2f}])")
        return None

    # Pick the contract closest to center of delta range
    target_delta = (delta_min + delta_max) / 2
    best = min(candidates, key=lambda c: abs(abs(c["delta"]) - target_delta))
    logger.info(
        f"{ticker} CSP selected: {best['symbol']} "
        f"strike={best['strike']} exp={best['expiration']} "
        f"delta={best['delta']:.3f} mid=${best['mid']:.2f}"
    )
    return best


def find_best_call(
    contracts: list[dict],
    ticker: str,
    iv_rank: float,
    vix_zone: str,
    cost_basis: float,
    adjusted_cost_basis: float,
    max_dte: int,
    rescue_mode: bool = False,
) -> Optional[dict]:
    """
    From a list of call contract snapshots, return the best CC candidate.
    Hard rule: strike must be >= adjusted_cost_basis.
    """
    delta_min, delta_max = compute_delta_target(ticker, iv_rank, vix_zone, "CC")
    logger.info(
        f"{ticker} CC delta target: [{delta_min:.2f}, {delta_max:.2f}] | "
        f"rescue={rescue_mode} | adj_cost_basis={adjusted_cost_basis:.2f}"
    )

    candidates = []
    for c in contracts:
        if c["type"] != "call":
            continue
        if c["dte"] > max_dte or c["dte"] < 2:
            continue

        # Hard rule: never sell below adjusted cost basis
        if c["strike"] < adjusted_cost_basis:
            continue

        if rescue_mode:
            # In rescue mode, sell at cost basis strike (low delta acceptable)
            if abs(c["strike"] - adjusted_cost_basis) > 2.5:
                continue
        else:
            abs_delta = abs(c["delta"])
            if not (delta_min <= abs_delta <= delta_max):
                continue

        if not check_spread(ticker, c["bid"], c["ask"]):
            continue
        if not check_premium_threshold(ticker, c["mid"], c["strike"], c["dte"]):
            # In rescue mode, be more lenient on premium
            if not rescue_mode:
                continue

        candidates.append(c)

    if not candidates:
        logger.warning(f"{ticker} CC: no qualifying call found")
        return None

    if rescue_mode:
        # Prefer the call with highest premium
        best = max(candidates, key=lambda c: c["mid"])
    else:
        target_delta = (delta_min + delta_max) / 2
        best = min(candidates, key=lambda c: abs(abs(c["delta"]) - target_delta))

    logger.info(
        f"{ticker} CC selected: {best['symbol']} "
        f"strike={best['strike']} exp={best['expiration']} "
        f"delta={best['delta']:.3f} mid=${best['mid']:.2f}"
    )
    return best


def calculate_limit_price(contract: dict, ticker: str, toward_natural: int = 0) -> float:
    """
    Compute limit price for selling:
    - TSLA: mid
    - PLTR: mid + $0.03 toward ask (to account for wider spreads)
    Adjust by `toward_natural` × step if retrying.
    """
    from bot.config import FILL_ADJUST_STEP
    mid = contract["mid"]
    step = FILL_ADJUST_STEP[ticker]

    if ticker == "TSLA":
        base = mid
    else:  # PLTR
        base = mid + 0.03

    # Each retry: move toward natural (ask for sells = move lower limit toward bid)
    adjusted = base - (toward_natural * step)
    return max(0.01, round(adjusted, 2))


def evaluate_roll_candidate(
    contract: dict,
    new_contract: dict,
    stage: str,
) -> bool:
    """
    A roll is only valid if it generates a net credit.
    cost_to_close = ask of existing (to buy back)
    premium_new   = bid of new (to sell)
    net_credit    = premium_new - cost_to_close
    """
    cost_to_close = contract.get("ask", contract["mid"] * 1.1)
    premium_new   = new_contract.get("bid", new_contract["mid"] * 0.9)
    net_credit    = premium_new - cost_to_close
    logger.info(
        f"Roll evaluation: close_cost=${cost_to_close:.2f} "
        f"new_premium=${premium_new:.2f} net_credit=${net_credit:.2f}"
    )
    return net_credit > 0.0
