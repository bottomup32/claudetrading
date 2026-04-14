"""
Daily summary report generator.
Produces the formatted report defined in the strategy spec.
"""
from datetime import date
from typing import Optional


def generate_daily_report(state: dict, ctx: dict) -> str:
    tickers_data = ctx.get("tickers", {})
    account      = ctx.get("account", {})
    vix          = ctx.get("vix", 0)
    vix_zone     = ctx.get("vix_zone", "?")
    earnings     = ctx.get("earnings", {})

    equity       = account.get("equity", 0)
    cash         = account.get("cash", 0)
    deployed     = equity - cash

    # Build positions table rows
    pos_rows = ""
    for ticker in ["TSLA", "PLTR"]:
        pos = state["positions"].get(ticker)
        if pos:
            exp     = pos.get("expiration", "?")
            dte     = (date.fromisoformat(exp) - date.today()).days if exp != "?" else "?"
            delta   = pos.get("delta_at_entry", 0)
            prem    = pos.get("premium_received", 0)
            stage   = pos.get("stage", "?")
            strike  = pos.get("strike", "?")
            pos_rows += (
                f"│ {ticker:<6} │ {stage:<5} │ ${strike:<7} │ {exp:<7} │ {str(dte):<5} │ "
                f"{'%.3f' % delta:<7} │ ${prem:<6.2f} │ {'?':>9} │\n"
            )

    if not pos_rows:
        pos_rows = "│ (no open options positions)                                                │\n"

    # Rescue and adjusted cost basis
    rescue_active = ""
    adj_cb_str    = ""
    for ticker in ["TSLA", "PLTR"]:
        pos = state["positions"].get(ticker)
        if pos and pos.get("adjusted_cost_basis"):
            adj_cb_str += f"  {ticker} adj cost basis: ${pos['adjusted_cost_basis']:.2f}   "
        if pos and pos.get("stage") == "CC":
            price = tickers_data.get(ticker, {}).get("price", 0)
            cost  = pos.get("assignment_price", 0)
            if price and cost and price < cost * 0.85:
                rescue_active += f"{ticker} "

    # Performance table
    perf_rows = ""
    total_cycles = total_prem = total_pnl = total_wins = total_losses = 0
    for ticker in ["TSLA", "PLTR"]:
        p = state["performance"].get(ticker, {})
        cyc  = p.get("cycles", 0)
        prem = p.get("total_premium", 0)
        pnl  = p.get("realized_pnl", 0)
        wins = p.get("wins", 0)
        losses = p.get("losses", 0)
        rate = f"{wins/(wins+losses):.0%}" if (wins + losses) > 0 else "N/A"
        perf_rows += (
            f"│ {ticker:<6} │ {cyc:<7} │ ${prem:<12,.0f} │ ${pnl:<12,.0f} │ {rate:<8} │\n"
        )
        total_cycles += cyc; total_prem += prem; total_pnl += pnl
        total_wins += wins; total_losses += losses

    total_rate = f"{total_wins/(total_wins+total_losses):.0%}" if (total_wins + total_losses) > 0 else "N/A"
    perf_rows += (
        f"├────────┼─────────┼──────────────┼──────────────┼──────────┤\n"
        f"│ TOTAL  │ {total_cycles:<7} │ ${total_prem:<12,.0f} │ ${total_pnl:<12,.0f} │ {total_rate:<8} │\n"
    )

    # Recent actions
    recent_actions = ""
    for act in state.get("action_log", [])[-5:]:
        ts     = act.get("timestamp", "")[:19].replace("T", " ")
        action = act.get("action", "")
        ticker = act.get("ticker", "")
        recent_actions += f"  {ts} [{action}] {ticker}\n"
    if not recent_actions:
        recent_actions = "  (none today)\n"

    # Upcoming events
    upcoming = ""
    for ticker in ["TSLA", "PLTR"]:
        earn_str = earnings.get(ticker)
        if earn_str:
            earn_date = date.fromisoformat(earn_str[:10])
            days_until = (earn_date - date.today()).days
            flag = " ⚠️" if days_until <= 21 else ""
            upcoming += f"  {ticker} Earnings: {earn_str} — {days_until} days{flag}\n"
        else:
            upcoming += f"  {ticker} Earnings: Unknown\n"

    # Ticker snapshot
    ticker_snap = ""
    for ticker in ["TSLA", "PLTR"]:
        td = tickers_data.get(ticker, {})
        ticker_snap += (
            f"  {ticker}: ${td.get('price',0):.2f} | "
            f"30D IV: {td.get('iv',0):.1%} | "
            f"IV Rank: {td.get('iv_rank',0):.1%} | "
            f"RSI: {td.get('rsi',0):.1f}\n"
        )

    report = f"""
═══════════════════════════════════════════════════════════════
              WHEEL STRATEGY — DAILY REPORT
              Date: {date.today().isoformat()} | Powered by Claude
═══════════════════════════════════════════════════════════════

MARKET CONTEXT
  VIX: {vix:.1f} | Zone: {vix_zone.upper()}

TICKER SNAPSHOT
{ticker_snap}
ACCOUNT OVERVIEW
  Total Equity:       ${equity:>12,.2f}
  Cash Available:     ${cash:>12,.2f}
  Capital Deployed:   ${deployed:>12,.2f}  ({deployed/equity:.1%} of account if equity > 0 else N/A)

ACTIVE POSITIONS
┌────────┬───────┬────────┬───────┬───────┬─────────┬────────┬───────────┐
│ Ticker │ Stage │ Strike │ Exp   │ DTE   │ Delta   │ Prem   │ P&L %     │
├────────┼───────┼────────┼───────┼───────┼─────────┼────────┼───────────┤
{pos_rows}└────────┴───────┴────────┴───────┴───────┴─────────┴────────┴───────────┘

  Rescue Mode Active: {'YES — ' + rescue_active if rescue_active else 'No'}
  {adj_cb_str}

ACTIONS TAKEN TODAY
{recent_actions}
CYCLE TRACKER
┌────────┬─────────┬──────────────┬──────────────┬──────────┐
│ Ticker │ Cycles  │ Total Prem   │ Realized P&L │ Win Rate │
├────────┼─────────┼──────────────┼──────────────┼──────────┤
{perf_rows}└────────┴─────────┴──────────────┴──────────────┴──────────┘

UPCOMING EVENTS
{upcoming}
═══════════════════════════════════════════════════════════════
"""
    return report
