"""
Analytics data logger — accumulates structured JSONL records for optimization.

Three files in data/analytics/:
  runs.jsonl      — one record per bot run (market context snapshot)
  decisions.jsonl — one record per trade decision (open/close/roll/skip)
  positions.jsonl — one record per position P&L snapshot at each check

All records include a UTC timestamp and are appended (never overwritten),
so the full history accumulates for analysis.
"""
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from bot.config import ROOT_DIR

logger = logging.getLogger(__name__)

ANALYTICS_DIR = ROOT_DIR / "data" / "analytics"
RUNS_FILE      = ANALYTICS_DIR / "runs.jsonl"
DECISIONS_FILE = ANALYTICS_DIR / "decisions.jsonl"
POSITIONS_FILE = ANALYTICS_DIR / "positions.jsonl"


def _ensure_dir():
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)


def _append(filepath: Path, record: dict):
    """Append one JSON record to a JSONL file."""
    _ensure_dir()
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logger.warning(f"data_logger: failed to write to {filepath.name}: {e}")


def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _iv_rank_bucket(iv_rank: float) -> str:
    if iv_rank < 0.40:
        return "cheap"
    elif iv_rank <= 0.65:
        return "normal"
    else:
        return "rich"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Run-level record — logged once per bot invocation
# ═══════════════════════════════════════════════════════════════════════════════

def log_run(
    task: str,
    vix: float,
    vix_zone: str,
    qqq_change: float,
    ticker_data: dict,       # {TSLA: {price, rsi, iv, iv_rank}, PLTR: {...}}
    account: dict,           # {equity, cash, buying_power}
    open_positions: int,
    state_summary: dict,     # {TSLA: stage|None, PLTR: stage|None}
):
    """Log one record per bot run to runs.jsonl."""
    record = {
        "ts":             _ts(),
        "date":           date.today().isoformat(),
        "task":           task,
        "vix":            round(vix, 2),
        "vix_zone":       vix_zone,
        "qqq_change_pct": round(qqq_change * 100, 3),
        "open_positions": open_positions,
        "account_equity": round(account.get("equity", 0), 2),
        "account_cash":   round(account.get("cash", 0), 2),
        "capital_deployed_pct": round(
            (account.get("equity", 0) - account.get("cash", 0)) / account.get("equity", 1) * 100, 1
        ),
        "positions": state_summary,
    }
    for ticker, td in ticker_data.items():
        record[ticker] = {
            "price":    round(td.get("price", 0), 2),
            "rsi":      round(td.get("rsi", 0), 1),
            "iv_pct":   round(td.get("iv", 0) * 100, 1),
            "iv_rank":  round(td.get("iv_rank", 0), 3),
            "iv_bucket": _iv_rank_bucket(td.get("iv_rank", 0)),
        }
    _append(RUNS_FILE, record)
    logger.debug(f"data_logger: run record logged ({task})")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Decision records — logged for every trade action or skip
# ═══════════════════════════════════════════════════════════════════════════════

def log_decision_open(
    ticker: str,
    stage: str,           # "CSP" or "CC"
    contract: str,
    strike: float,
    expiration: str,
    dte: int,
    delta: float,
    premium: float,
    iv: float,
    iv_rank: float,
    vix: float,
    vix_zone: str,
    rsi: float,
    stock_price: float,
    otm_pct: float,       # (stock_price - strike) / stock_price for put
    ann_return_pct: float,
):
    record = {
        "ts":            _ts(),
        "date":          date.today().isoformat(),
        "ticker":        ticker,
        "decision":      f"OPEN_{stage}",
        "contract":      contract,
        "strike":        strike,
        "expiration":    expiration,
        "dte":           dte,
        "delta":         round(delta, 4),
        "premium":       round(premium, 2),
        "iv_pct":        round(iv * 100, 1),
        "iv_rank":       round(iv_rank, 3),
        "iv_bucket":     _iv_rank_bucket(iv_rank),
        "vix":           round(vix, 2),
        "vix_zone":      vix_zone,
        "rsi":           round(rsi, 1),
        "stock_price":   round(stock_price, 2),
        "otm_pct":       round(otm_pct * 100, 2),
        "ann_return_pct": round(ann_return_pct, 1),
    }
    _append(DECISIONS_FILE, record)


def log_decision_close(
    ticker: str,
    stage: str,
    contract: str,
    entry_premium: float,
    close_price: float,
    profit_pct: float,
    dte_remaining: int,
    reason: str,           # e.g. "profit_50pct", "gamma_risk", "earnings_blackout"
    stock_price: float,
    iv_rank: float,
    vix: float,
):
    record = {
        "ts":            _ts(),
        "date":          date.today().isoformat(),
        "ticker":        ticker,
        "decision":      f"CLOSE_{stage}",
        "contract":      contract,
        "entry_premium": round(entry_premium, 2),
        "close_price":   round(close_price, 2),
        "profit_pct":    round(profit_pct * 100, 1),
        "dte_remaining": dte_remaining,
        "reason":        reason,
        "stock_price":   round(stock_price, 2),
        "iv_rank":       round(iv_rank, 3),
        "iv_bucket":     _iv_rank_bucket(iv_rank),
        "vix":           round(vix, 2),
        "outcome":       "WIN" if profit_pct > 0 else "LOSS",
    }
    _append(DECISIONS_FILE, record)


def log_decision_skip(
    ticker: str,
    stage: str,         # "CSP" or "CC"
    reason: str,
    details: dict,      # arbitrary extra context (rsi value, iv_rank, etc.)
    vix: float,
    vix_zone: str,
):
    record = {
        "ts":       _ts(),
        "date":     date.today().isoformat(),
        "ticker":   ticker,
        "decision": f"SKIP_{stage}",
        "reason":   reason,
        "vix":      round(vix, 2),
        "vix_zone": vix_zone,
        **{k: round(v, 4) if isinstance(v, float) else v for k, v in details.items()},
    }
    _append(DECISIONS_FILE, record)


def log_decision_roll(
    ticker: str,
    stage: str,
    old_contract: str,
    new_contract: str,
    old_strike: float,
    new_strike: float,
    old_expiration: str,
    new_expiration: str,
    net_credit: float,
    direction: str,     # "up" or "down"
    rolls_count: int,
    stock_price: float,
    vix: float,
):
    record = {
        "ts":             _ts(),
        "date":           date.today().isoformat(),
        "ticker":         ticker,
        "decision":       f"ROLL_{stage}",
        "direction":      direction,
        "old_contract":   old_contract,
        "new_contract":   new_contract,
        "old_strike":     old_strike,
        "new_strike":     new_strike,
        "old_expiration": old_expiration,
        "new_expiration": new_expiration,
        "net_credit":     round(net_credit, 2),
        "roll_number":    rolls_count,
        "stock_price":    round(stock_price, 2),
        "vix":            round(vix, 2),
    }
    _append(DECISIONS_FILE, record)


def log_decision_assignment(
    ticker: str,
    strike: float,
    total_csp_premiums: float,
    adj_cost_basis: float,
    stock_price_at_assignment: float,
    iv_rank: float,
    vix: float,
):
    record = {
        "ts":                  _ts(),
        "date":                date.today().isoformat(),
        "ticker":              ticker,
        "decision":            "ASSIGNMENT",
        "strike":              strike,
        "total_csp_premiums":  round(total_csp_premiums, 2),
        "adj_cost_basis":      round(adj_cost_basis, 2),
        "stock_at_assignment": round(stock_price_at_assignment, 2),
        "loss_vs_strike_pct":  round((stock_price_at_assignment - strike) / strike * 100, 2),
        "iv_rank":             round(iv_rank, 3),
        "vix":                 round(vix, 2),
    }
    _append(DECISIONS_FILE, record)


def log_decision_expiry(
    ticker: str,
    stage: str,
    contract: str,
    entry_premium: float,
    strike: float,
    stock_price: float,
    iv_rank: float,
    vix: float,
):
    record = {
        "ts":            _ts(),
        "date":          date.today().isoformat(),
        "ticker":        ticker,
        "decision":      f"EXPIRED_WORTHLESS_{stage}",
        "contract":      contract,
        "entry_premium": round(entry_premium, 2),
        "strike":        strike,
        "stock_price":   round(stock_price, 2),
        "otm_at_expiry_pct": round((stock_price - strike) / strike * 100, 2),
        "iv_rank":       round(iv_rank, 3),
        "iv_bucket":     _iv_rank_bucket(iv_rank),
        "vix":           round(vix, 2),
        "outcome":       "WIN",
    }
    _append(DECISIONS_FILE, record)


def log_circuit_breaker(ticker: str, loss_pct: float, vix: float):
    record = {
        "ts":       _ts(),
        "date":     date.today().isoformat(),
        "ticker":   ticker,
        "decision": "CIRCUIT_BREAKER",
        "loss_pct": round(loss_pct * 100, 2),
        "vix":      round(vix, 2),
    }
    _append(DECISIONS_FILE, record)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Position snapshots — logged at every profit-target check
# ═══════════════════════════════════════════════════════════════════════════════

def log_position_snapshot(
    ticker: str,
    task: str,
    stage: str,
    contract: str,
    strike: float,
    expiration: str,
    dte: int,
    entry_premium: float,
    current_mid: float,
    profit_pct: float,
    delta_now: float,
    iv_now: float,
    stock_price: float,
    vix: float,
    rescue_mode: bool = False,
):
    record = {
        "ts":            _ts(),
        "date":          date.today().isoformat(),
        "task":          task,
        "ticker":        ticker,
        "stage":         stage,
        "contract":      contract,
        "strike":        strike,
        "expiration":    expiration,
        "dte":           dte,
        "entry_premium": round(entry_premium, 2),
        "current_mid":   round(current_mid, 2),
        "profit_pct":    round(profit_pct * 100, 1),
        "delta_now":     round(delta_now, 4),
        "iv_now_pct":    round(iv_now * 100, 1),
        "stock_price":   round(stock_price, 2),
        "vix":           round(vix, 2),
        "rescue_mode":   rescue_mode,
        "theta_decay_status": (
            "near_target" if profit_pct >= 0.40
            else "on_track" if profit_pct >= 0.20
            else "early"
        ),
    }
    _append(POSITIONS_FILE, record)
