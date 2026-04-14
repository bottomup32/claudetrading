"""
Persistent state management via JSON.
All bot state (positions, performance, IV history, action log) lives here.
"""
import json
import logging
from datetime import datetime, date
from typing import Optional
from bot.config import STATE_FILE, DATA_DIR

logger = logging.getLogger(__name__)

# ── Default blank state ───────────────────────────────────────────────────────
DEFAULT_STATE = {
    "positions": {
        "TSLA": None,
        "PLTR": None,
    },
    "performance": {
        "TSLA": {"cycles": 0, "total_premium": 0.0, "realized_pnl": 0.0, "wins": 0, "losses": 0, "assignments": 0},
        "PLTR": {"cycles": 0, "total_premium": 0.0, "realized_pnl": 0.0, "wins": 0, "losses": 0, "assignments": 0},
    },
    "iv_history": {
        "TSLA": [],   # [{date: "YYYY-MM-DD", iv: 0.55}, ...]
        "PLTR": [],
    },
    "action_log": [],
    "last_entry_date": {"TSLA": None, "PLTR": None},
    "last_exit_date":  {"TSLA": None, "PLTR": None},
    "sector_stress_freeze_until": None,
    "earnings": {
        "TSLA": None,   # "YYYY-MM-DD"
        "PLTR": None,
    },
}

# ── Position schema ───────────────────────────────────────────────────────────
def blank_position(ticker: str, stage: str, contract_symbol: str,
                   strike: float, expiration: str, dte: int,
                   premium: float, delta: float, iv_rank: float, vix: float) -> dict:
    return {
        "ticker": ticker,
        "stage": stage,               # "CSP" or "CC"
        "contract": contract_symbol,
        "strike": strike,
        "expiration": expiration,
        "entry_date": date.today().isoformat(),
        "dte_at_entry": dte,
        "premium_received": premium,
        "delta_at_entry": delta,
        "iv_rank_at_entry": iv_rank,
        "vix_at_entry": vix,
        "rolls_count": 0,
        # CC / assignment fields
        "shares": 0,
        "assignment_price": None,
        "adjusted_cost_basis": None,
        "total_premiums_this_lot": premium,
        # Tracking
        "open_order_id": None,
        "position_closed": False,
    }


def load_state() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        save_state(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        # Merge any missing top-level keys from defaults
        for key, val in DEFAULT_STATE.items():
            if key not in state:
                state[key] = val
        return state
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load state: {e} — starting fresh")
        return DEFAULT_STATE.copy()


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def log_action(state: dict, action: str, details: dict) -> None:
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        **details,
    }
    state["action_log"].append(entry)
    logger.info(f"[ACTION] {action}: {details}")
    # Keep last 500 entries to prevent unbounded growth
    if len(state["action_log"]) > 500:
        state["action_log"] = state["action_log"][-500:]


def update_iv_history(state: dict, ticker: str, current_iv: float) -> None:
    today = date.today().isoformat()
    history = state["iv_history"][ticker]
    # Replace today's entry if already recorded
    if history and history[-1]["date"] == today:
        history[-1]["iv"] = current_iv
    else:
        history.append({"date": today, "iv": current_iv})
    # Keep rolling 365-day window
    if len(history) > 365:
        state["iv_history"][ticker] = history[-365:]


def calculate_iv_rank(state: dict, ticker: str, current_iv: float) -> float:
    """
    IV Rank = (current - 52w_low) / (52w_high - 52w_low)
    Falls back to config bounds if < 30 days of history.
    """
    from bot.config import IV_52W
    history = state["iv_history"].get(ticker, [])
    if len(history) >= 30:
        ivs = [h["iv"] for h in history[-252:]]  # up to 1 year
        iv_low  = min(ivs)
        iv_high = max(ivs)
    else:
        iv_low  = IV_52W[ticker]["low"]
        iv_high = IV_52W[ticker]["high"]

    if iv_high == iv_low:
        return 0.50  # can't divide by zero
    rank = (current_iv - iv_low) / (iv_high - iv_low)
    return max(0.0, min(1.0, rank))


def count_open_csps(state: dict) -> int:
    count = 0
    for pos in state["positions"].values():
        if pos and pos["stage"] == "CSP":
            count += 1
    return count


def count_open_positions(state: dict) -> int:
    return sum(1 for pos in state["positions"].values() if pos is not None)
