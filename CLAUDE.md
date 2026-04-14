# ClaudeTrading — Adaptive Wheel Strategy Bot

## Project Purpose
Automated options wheel strategy on **TSLA** and **PLTR** using Alpaca Paper Trading.
Runs 5×/day via GitHub Actions even when the user's PC is off.
Strategy spec: `docs/strategy_v2.1.md` (or the inline comments in `bot/config.py`).

---

## Architecture

```
ClaudeTrading/
├── bot/
│   ├── config.py          ← ALL strategy constants (deltas, thresholds, circuit breakers)
│   ├── state_manager.py   ← JSON state persistence + IV Rank calculation
│   ├── alpaca_client.py   ← Alpaca trading + options data API wrapper
│   ├── market_data.py     ← VIX, RSI, IV, QQQ, earnings via yfinance
│   ├── options_engine.py  ← Strike selection (dual-layer delta + VIX shift)
│   ├── order_manager.py   ← Order execution with per-ticker retry logic
│   ├── strategy.py        ← Main decision engine (all 12 safety rules)
│   ├── reporter.py        ← Daily summary report
│   ├── data_logger.py     ← Structured analytics data collection (JSONL)
│   └── main.py            ← Entry point, --task routing
├── data/
│   ├── state.json         ← Live bot state (positions, performance, IV history)
│   └── analytics/
│       ├── runs.jsonl         ← One record per bot run (market context snapshot)
│       ├── decisions.jsonl    ← One record per trade decision (open/close/roll/skip)
│       └── positions.jsonl    ← One record per position snapshot (P&L, Greeks at check time)
├── logs/
│   ├── bot_YYYYMMDD.log   ← Raw bot log per day
│   └── daily_YYYY-MM-DD.txt ← Formatted daily report
├── .github/workflows/
│   └── wheel_bot.yml      ← GitHub Actions scheduler (5×/day, Mon-Fri ET)
└── requirements.txt
```

---

## Strategy Summary (v2.1)

### Two-Stage Cycle
1. **Stage 1 — Sell Cash-Secured Put (CSP)**: Collect premium, risk assignment
2. **Stage 2 — Sell Covered Call (CC)**: After assignment, collect premium, risk having shares called away
3. Repeat

### Key Parameters (all in `bot/config.py`)

| Parameter | TSLA | PLTR |
|-----------|------|------|
| CSP Delta (normal IV) | -0.18 to -0.22 | -0.15 to -0.20 |
| CC Delta (normal IV) | +0.22 to +0.28 | +0.20 to +0.25 |
| Max DTE (CSP) | 14 days | 10 days |
| Min Annualized Return | 20% | 25% |
| Max Bid-Ask Spread | $0.30 | $0.20 |
| Rescue Mode Trigger | < 85% of cost basis | < 85% of cost basis |
| Circuit Breaker | -30% unrealized | -25% unrealized |

### VIX Regime Engine
| Zone | VIX | Delta OTM Shift | Max Positions | Max DTE |
|------|-----|-----------------|---------------|---------|
| Low | < 20 | +0.00 | 2 | 14d |
| Normal | 20–28 | +0.03 OTM | 1 | 14d |
| Elevated | 28–35 | +0.05 OTM | 1 | 7d |
| Extreme | > 35 | +0.08 OTM | 1 | 7d |

### Safety Rules (non-negotiable, enforced in `bot/strategy.py`)
1. Never naked puts — always fully cash-secured
2. Never CC below adjusted cost basis
3. Max 1 CSP at a time across both tickers
4. Never exceed 75% total capital deployment
5. 10-day earnings blackout (absolute)
6. Never roll for net debit
7. Circuit breakers: TSLA -30%, PLTR -25% → manual review only
8. Regular market hours only (9:30–4:00 PM ET)
9. Always limit orders (never market orders on options)

---

## Schedule (GitHub Actions)

| ET Time | UTC | Task | What it does |
|---------|-----|------|--------------|
| 9:35 AM | 13:35 | `morning_scan` | VIX/RSI/IV refresh, assignment check, new CSP entry |
| 11:00 AM | 15:00 | `midmorning_check` | 50% profit check, fill confirmation |
| 1:00 PM | 17:00 | `midday_review` | Delta drift, rolling candidates |
| 3:00 PM | 19:00 | `afternoon_check` | Profit targets, earnings proximity |
| 3:50 PM | 19:50 | `preclose` | Final profit-take, daily report, cancel unfilled orders |

---

## Alpaca API Notes

- **Paper trading URL**: `https://paper-api.alpaca.markets`
- **Key files**: credentials in `bot/config.py` (env vars override in production)
- **Assignment detection**: poll `GET /v2/account/activities` for `OPASN`, `OPEXP`, `OPEXC`
- **Options chain**: `OptionHistoricalDataClient.get_option_chain(OptionChainRequest(...))`
- **Option snapshots** include Greeks (delta, gamma, theta, vega) + implied_volatility
- **Order format**: OCC symbol e.g. `TSLA250418P00300000`

---

## State Schema (`data/state.json`)

```json
{
  "positions": {
    "TSLA": {
      "stage": "CSP|CC",
      "contract": "TSLA250418P00300000",
      "strike": 300.0,
      "expiration": "2025-04-18",
      "entry_date": "2025-04-14",
      "premium_received": 5.20,
      "delta_at_entry": -0.20,
      "iv_rank_at_entry": 0.55,
      "vix_at_entry": 18.5,
      "rolls_count": 0,
      "shares": 0,
      "assignment_price": null,
      "adjusted_cost_basis": null,
      "total_premiums_this_lot": 5.20
    },
    "PLTR": null
  },
  "performance": {
    "TSLA": {"cycles": 0, "total_premium": 0, "realized_pnl": 0, "wins": 0, "losses": 0, "assignments": 0}
  },
  "iv_history": {
    "TSLA": [{"date": "YYYY-MM-DD", "iv": 0.52}]
  },
  "action_log": [{"timestamp": "...", "action": "OPEN_CSP", "ticker": "TSLA", ...}]
}
```

---

## Analytics Data (`data/analytics/`)

Three JSONL files accumulate over time. **Do not delete these** — they are the optimization dataset.

### `runs.jsonl` — Market context at each bot run
```json
{"ts": "2026-04-14T13:35:00Z", "task": "morning_scan", "vix": 18.5, "vix_zone": "low",
 "qqq_change": -0.012, "TSLA": {"price": 285.4, "rsi": 48.2, "iv": 0.523, "iv_rank": 0.61},
 "PLTR": {"price": 92.3, "rsi": 52.1, "iv": 0.681, "iv_rank": 0.72},
 "open_positions": 1, "account_equity": 100000, "cash": 65000}
```

### `decisions.jsonl` — Every trade decision (open/close/roll/skip)
```json
{"ts": "2026-04-14T13:35:00Z", "ticker": "TSLA", "decision": "OPEN_CSP",
 "contract": "TSLA250418P00285000", "strike": 285, "expiration": "2026-04-18",
 "dte": 4, "delta": -0.20, "premium": 5.20, "iv_rank": 0.61, "vix": 18.5,
 "vix_zone": "low", "reason": "all_conditions_met"}

{"ts": "2026-04-14T15:00:00Z", "ticker": "PLTR", "decision": "SKIP_CSP",
 "reason": "rsi_out_of_range", "rsi": 78.2, "iv_rank": 0.55, "vix": 18.5}
```

### `positions.jsonl` — Position snapshot at each check
```json
{"ts": "2026-04-14T15:00:00Z", "ticker": "TSLA", "stage": "CSP",
 "contract": "TSLA250418P00285000", "strike": 285, "expiration": "2026-04-18",
 "dte": 4, "entry_premium": 5.20, "current_mid": 2.80, "profit_pct": 0.462,
 "delta_now": -0.12, "iv_now": 0.48, "stock_price": 291.2}
```

---

## How to Add/Change Strategy Rules

1. **Change a threshold** (e.g., delta target, profit take %):
   - Edit `bot/config.py` — all constants are there
   - The strategy engine reads from config at runtime

2. **Change entry/exit logic**:
   - Edit `bot/strategy.py` → `_csp_entry_check()` for entry gates
   - Edit `bot/strategy.py` → `_check_profit_targets()` for exit logic

3. **Change strike selection**:
   - Edit `bot/options_engine.py` → `find_best_put()` or `find_best_call()`

4. **Add a new ticker**:
   - Add to `TICKERS` list in `bot/config.py`
   - Add IV 52-week range to `IV_52W`
   - Add delta targets to `CSP_DELTA` and `CC_DELTA`
   - Add thresholds to `MIN_ANNUALIZED_RETURN`, `MAX_SPREAD`, `CIRCUIT_BREAKER`, etc.

---

## How to Analyze Optimization Data

```python
import pandas as pd, json

# Load all decision records
decisions = pd.read_json("data/analytics/decisions.jsonl", lines=True)

# Load all run records
runs = pd.read_json("data/analytics/runs.jsonl", lines=True)

# Load position snapshots
positions = pd.read_json("data/analytics/positions.jsonl", lines=True)

# Example: win rate by IV rank bucket
decisions[decisions["decision"].isin(["CLOSE_PROFIT", "EXPIRED_WORTHLESS"])].groupby("iv_rank_bucket")["profit_pct"].mean()

# Example: average premium by VIX zone
decisions[decisions["decision"] == "OPEN_CSP"].groupby("vix_zone")["premium"].mean()
```

---

## Optimization Checklist (Things to Tune)

Track these questions as data accumulates:

| Question | Data Needed | Config Key |
|----------|-------------|------------|
| Is 50% profit take optimal? | `decisions.jsonl` profit_pct at close | `PROFIT_TAKE_50PCT` |
| Are delta targets right for current IV regime? | `decisions.jsonl` delta vs outcome | `CSP_DELTA`, `CC_DELTA` |
| Is 10-day earnings blackout too conservative? | Miss rate near earnings | `EARNINGS_BLACKOUT_DAYS` |
| Is RSI 35–75 filter killing good trades? | `decisions.jsonl` SKIP_CSP reason=rsi | `RSI_MIN`, `RSI_MAX` |
| Which VIX zone gives best risk-adjusted return? | `runs.jsonl` + `decisions.jsonl` | `VIX_ZONE_PARAMS` |
| Is PLTR or TSLA more profitable? | `decisions.jsonl` by ticker | N/A — comparison |
| Should max DTE be longer in low VIX? | Position duration vs premium | `CSP_MAX_DTE`, `CC_MAX_DTE` |
| Is 85% rescue trigger right for these tickers? | Assignment rate, rescue activation | `RESCUE_TRIGGER_PCT` |

---

## Deployment

- **Cloud**: GitHub Actions (free tier, runs Mon-Fri)
- **Repo**: `https://github.com/bottomup32/claudetrading`
- **Secrets needed**: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
- **Manual trigger**: Actions tab → "Adaptive Wheel Strategy Bot" → "Run workflow"
- **State persistence**: Bot commits `data/state.json` + `logs/` + `data/analytics/` after each run

## Credentials (Paper Trading Only)
- Stored in memory. Use env vars `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in GitHub Secrets.
- Paper URL: `https://paper-api.alpaca.markets`

---

## Known Limitations & Future Work

- **IV Rank accuracy**: Uses yfinance ATM option IV as proxy. First 30 days use hardcoded 52-week ranges from strategy doc. Gets more accurate as `iv_history` accumulates.
- **Assignment detection**: Polls activities endpoint every 30 min during market hours. Not real-time.
- **No WebSocket**: All data is polled — no live streaming.
- **Paper trading only**: Live trading requires different Alpaca endpoint + additional risk review.
- **Holiday handling**: `is_market_open()` does not check NYSE holidays — may attempt to run on market holidays (will fail gracefully since no data/orders will process).
