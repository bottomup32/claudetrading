"""
Adaptive Wheel Strategy — Configuration
All strategy constants in one place.
"""
import os

# ── Alpaca credentials (set as env vars in GitHub Actions secrets) ───────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY",    "PKUY7XNURSF2AWZHOJJ6OI7DC5")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "2xgHCxkzoXbdt8ASqJ3177mF6M4sSTmnzX3gs8V2e92C")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL",   "https://paper-api.alpaca.markets")
PAPER_TRADING     = True

# ── Tickers ──────────────────────────────────────────────────────────────────
TICKERS = ["TSLA", "PLTR"]

# ── IV Rank 52-week bounds (from strategy doc) ────────────────────────────────
IV_52W = {
    "TSLA": {"low": 0.30, "high": 1.00},
    "PLTR": {"low": 0.40, "high": 1.00},
}

# ── VIX Regime Boundaries ────────────────────────────────────────────────────
VIX_ZONES = {
    "low":      (0,   20),
    "normal":   (20,  28),
    "elevated": (28,  35),
    "extreme":  (35, 999),
}

VIX_ZONE_PARAMS = {
    # (max_capital_pct, max_open_positions, max_dte, delta_otm_shift)
    "low":      (0.70, 2, 14, 0.00),
    "normal":   (0.55, 1, 14, 0.03),
    "elevated": (0.35, 1,  7, 0.05),
    "extreme":  (0.20, 1,  7, 0.08),
}

# ── Per-Ticker Delta Targets for CSP (Stage 1) ───────────────────────────────
# Keys: iv_rank buckets  → values: (delta_min, delta_max) as absolute values
CSP_DELTA = {
    "TSLA": {
        "cheap":  (0.22, 0.25),   # IV Rank < 40
        "normal": (0.18, 0.22),   # IV Rank 40–65
        "rich":   (0.12, 0.18),   # IV Rank > 65
    },
    "PLTR": {
        "cheap":  (0.20, 0.23),
        "normal": (0.15, 0.20),
        "rich":   (0.10, 0.15),
    },
}
CSP_MAX_DTE = {"TSLA": 14, "PLTR": 10}

# ── Per-Ticker Delta Targets for CC (Stage 2) ────────────────────────────────
CC_DELTA = {
    "TSLA": {
        "cheap":  (0.28, 0.32),
        "normal": (0.22, 0.28),
        "rich":   (0.18, 0.22),
    },
    "PLTR": {
        "cheap":  (0.25, 0.30),
        "normal": (0.20, 0.25),
        "rich":   (0.15, 0.20),
    },
}
CC_MAX_DTE = {"TSLA": 14, "PLTR": 10}

# ── Premium Minimum Annualized Return Threshold ───────────────────────────────
MIN_ANNUALIZED_RETURN = {"TSLA": 0.20, "PLTR": 0.25}

# ── Bid-Ask Spread Limits ────────────────────────────────────────────────────
MAX_SPREAD = {"TSLA": 0.30, "PLTR": 0.20}

# ── IV Rank Entry Filters ─────────────────────────────────────────────────────
IV_RANK_MIN_ENTRY  = 0.25   # below this → no entry
IV_RANK_LOW_ZONE   = 0.15   # 15–25: only low VIX + ≤7 DTE
IV_RANK_PREFERRED  = 0.50   # preferred zone starts here

# ── RSI Filter ───────────────────────────────────────────────────────────────
RSI_MIN = 35
RSI_MAX = 75

# ── Earnings Blackout ─────────────────────────────────────────────────────────
EARNINGS_BLACKOUT_DAYS = 10     # no open new position if earnings within this many days of expiry
POST_EARNINGS_WAIT     = 2      # wait 2 trading days after earnings before re-entry

# ── Profit Take Thresholds ────────────────────────────────────────────────────
# (profit_pct, close_if_dte_gt)
PROFIT_TAKE_40PCT_DTE = {"TSLA": 5, "PLTR": 3}   # close at 40% if DTE > this value
PROFIT_TAKE_50PCT     = 0.50    # always close at 50%
PROFIT_TAKE_GAMMA     = 0.65    # close at 65% if DTE < 3
GAMMA_DTE_THRESHOLD   = 3

# ── Rescue Mode ───────────────────────────────────────────────────────────────
RESCUE_TRIGGER_PCT    = 0.85    # stock < 85% of cost basis
CIRCUIT_BREAKER       = {"TSLA": -0.30, "PLTR": -0.25}

# ── Rolling Rules ─────────────────────────────────────────────────────────────
ROLL_TRIGGER_DROP_PCT  = {"TSLA": 0.08, "PLTR": 0.10}   # CSP roll-down trigger
ROLL_TRIGGER_SURGE_PCT = {"TSLA": 0.08, "PLTR": 0.10}   # CC roll-up trigger
MAX_ROLLS              = {"TSLA": 2, "PLTR": 1}

# ── Concentration Guard ───────────────────────────────────────────────────────
MAX_CAPITAL_PCT        = {"TSLA": 0.50, "PLTR": 0.40}
MAX_TOTAL_DEPLOYED_PCT = 0.75

# ── Sector Stress (QQQ) ───────────────────────────────────────────────────────
QQQ_DROP_FREEZE_PCT    = 0.03   # freeze new CSPs if QQQ drops > 3% in a day
SECTOR_FREEZE_HOURS    = 24

# ── Order Fill Management ─────────────────────────────────────────────────────
FILL_WAIT_SECONDS      = {"TSLA": 180, "PLTR": 300}     # wait before adjusting
FILL_ADJUST_STEP       = {"TSLA": 0.02, "PLTR": 0.03}   # step toward natural
MAX_FILL_ATTEMPTS      = {"TSLA": 3, "PLTR": 2}
PLTR_WIDE_SPREAD_WAIT  = 900  # 15 min wait if PLTR spread > $0.25 at entry

# ── Cooldown After Cycle Close ────────────────────────────────────────────────
CSP_REENTRY_COOLDOWN_SESSIONS = 1
CC_REENTRY_COOLDOWN_SESSIONS  = 0

# ── IV Rank Bucket Boundaries ─────────────────────────────────────────────────
def iv_rank_bucket(iv_rank: float) -> str:
    if iv_rank < 0.40:
        return "cheap"
    elif iv_rank <= 0.65:
        return "normal"
    else:
        return "rich"

# ── File Paths ────────────────────────────────────────────────────────────────
import pathlib
ROOT_DIR  = pathlib.Path(__file__).parent.parent
DATA_DIR  = ROOT_DIR / "data"
LOG_DIR   = ROOT_DIR / "logs"
STATE_FILE = DATA_DIR / "state.json"
