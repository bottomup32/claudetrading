"""
Main strategy decision engine.
Implements all rules from the Adaptive Wheel Strategy v2.1.
"""
import logging
from datetime import date, timedelta
from typing import Optional

from bot import config
from bot.config import (
    TICKERS, CSP_MAX_DTE, CC_MAX_DTE,
    IV_RANK_MIN_ENTRY, IV_RANK_LOW_ZONE,
    RSI_MIN, RSI_MAX, EARNINGS_BLACKOUT_DAYS, POST_EARNINGS_WAIT,
    EARNINGS_CONSERVATIVE_PROFIT_TAKE,
    PROFIT_TAKE_40PCT_DTE, PROFIT_TAKE_50PCT, PROFIT_TAKE_GAMMA, GAMMA_DTE_THRESHOLD,
    RESCUE_TRIGGER_PCT, CIRCUIT_BREAKER,
    ROLL_TRIGGER_DROP_PCT, ROLL_TRIGGER_SURGE_PCT, MAX_ROLLS,
    MAX_CAPITAL_PCT, MAX_TOTAL_DEPLOYED_PCT, QQQ_DROP_FREEZE_PCT, SECTOR_FREEZE_HOURS,
    CSP_REENTRY_COOLDOWN_SESSIONS,
)
from bot.state_manager import (
    load_state, save_state, log_action, update_iv_history,
    calculate_iv_rank, count_open_csps, count_open_positions,
    blank_position,
)
from bot.alpaca_client import AlpacaClient
from bot.market_data import (
    get_vix, get_vix_zone, get_vix_zone_params,
    get_rsi, get_current_iv, get_stock_price_yf,
    get_qqq_daily_change, get_earnings_dates, days_until_earnings,
)
from bot.options_engine import (
    find_best_put, find_best_call, evaluate_roll_candidate,
)
from bot.order_manager import (
    sell_option, buy_to_close, get_current_option_snapshot, check_profit_pct,
)
from bot.data_logger import (
    log_run, log_decision_open, log_decision_close, log_decision_skip,
    log_decision_roll, log_decision_assignment, log_decision_expiry,
    log_circuit_breaker, log_position_snapshot,
)

logger = logging.getLogger(__name__)


class WheelStrategy:
    def __init__(self):
        self.client = AlpacaClient()
        self.state  = load_state()
        self._market_context: dict = {}
        self._current_task: str = "unknown"

    # ═══════════════════════════════════════════════════════════════════════════
    # Public entry points (called by main.py per schedule)
    # ═══════════════════════════════════════════════════════════════════════════

    def morning_scan(self):
        """9:35 AM ET — Full morning check."""
        logger.info("=== MORNING SCAN ===")
        self._current_task = "morning_scan"
        self._refresh_market_context()
        self._check_assignments()
        self._check_profit_targets()
        self._check_sector_stress()
        self._evaluate_new_entries()
        self._check_earnings_proximity()
        save_state(self.state)

    def midmorning_check(self):
        """11:00 AM ET — Fill confirmation + 50% profit check."""
        logger.info("=== MID-MORNING CHECK ===")
        self._current_task = "midmorning_check"
        self._refresh_market_context()
        self._check_profit_targets()
        self._confirm_fills()
        save_state(self.state)

    def midday_review(self):
        """1:00 PM ET — Greeks, delta drift, rolling candidates."""
        logger.info("=== MIDDAY REVIEW ===")
        self._current_task = "midday_review"
        self._refresh_market_context()
        self._check_profit_targets()
        self._evaluate_rolling()
        save_state(self.state)

    def afternoon_check(self):
        """3:00 PM ET — Profit targets + catalyst scan."""
        logger.info("=== AFTERNOON CHECK ===")
        self._current_task = "afternoon_check"
        self._refresh_market_context()
        self._check_profit_targets()
        self._check_earnings_proximity()
        save_state(self.state)

    def preclose(self):
        """3:50 PM ET — Final decisions, unfilled order cleanup."""
        logger.info("=== PRE-CLOSE ===")
        self._current_task = "preclose"
        self._refresh_market_context()
        self._check_profit_targets()
        self._cancel_unfilled_orders()
        self._generate_daily_summary()
        save_state(self.state)

    # ═══════════════════════════════════════════════════════════════════════════
    # Core helpers
    # ═══════════════════════════════════════════════════════════════════════════

    def _refresh_market_context(self):
        """Collect VIX, RSI, IV, earnings, QQQ, account info."""
        vix       = get_vix()
        vix_zone  = get_vix_zone(vix)
        vix_params = get_vix_zone_params(vix_zone)
        account   = self.client.get_account()
        qqq_chg   = get_qqq_daily_change()
        earnings  = get_earnings_dates()

        ticker_data = {}
        for t in TICKERS:
            price = get_stock_price_yf(t)
            rsi   = get_rsi(t)
            iv    = get_current_iv(t)
            update_iv_history(self.state, t, iv)
            iv_rank = calculate_iv_rank(self.state, t, iv)
            ticker_data[t] = {
                "price":   price,
                "rsi":     rsi,
                "iv":      iv,
                "iv_rank": iv_rank,
            }
            logger.info(f"{t}: price=${price:.2f} RSI={rsi:.1f} IV={iv:.1%} IVR={iv_rank:.1%}")

        self._market_context = {
            "vix":        vix,
            "vix_zone":   vix_zone,
            "vix_params": vix_params,
            "account":    account,
            "qqq_change": qqq_chg,
            "earnings":   earnings,
            "tickers":    ticker_data,
        }
        # Persist earnings dates for daily reference
        self.state["earnings"] = earnings
        logger.info(f"VIX={vix:.1f} [{vix_zone}] | QQQ={qqq_chg:.2%} | cash=${account['cash']:,.0f}")

        # ── Analytics: log every run ──────────────────────────────────────────
        log_run(
            task=self._current_task,
            vix=vix,
            vix_zone=vix_zone,
            qqq_change=qqq_chg,
            ticker_data=ticker_data,
            account=account,
            open_positions=count_open_positions(self.state),
            state_summary={
                t: (self.state["positions"][t]["stage"] if self.state["positions"][t] else None)
                for t in TICKERS
            },
        )

    def _check_assignments(self):
        """Poll Alpaca activities for option assignment events (OPASN)."""
        activities = self.client.get_activities(["OPASN", "OPEXP", "OPEXC"])
        for act in activities:
            activity_type = act.get("activity_type") or act.get("type")
            symbol = act.get("symbol", "")
            ticker = next((t for t in TICKERS if symbol.startswith(t)), None)
            if not ticker:
                continue

            if activity_type == "OPASN":
                logger.info(f"ASSIGNMENT detected: {symbol} for {ticker}")
                pos = self.state["positions"].get(ticker)
                if pos and pos["stage"] == "CSP":
                    assignment_price = pos["strike"]
                    total_premiums   = pos["total_premiums_this_lot"]
                    adj_cost_basis   = assignment_price - total_premiums / 100  # per share

                    self.state["positions"][ticker] = {
                        **pos,
                        "stage":               "CC",
                        "shares":              100,
                        "assignment_price":    assignment_price,
                        "adjusted_cost_basis": adj_cost_basis,
                        "contract":            None,   # no CC open yet
                    }
                    self.state["performance"][ticker]["assignments"] += 1
                    log_action(self.state, "ASSIGNMENT", {
                        "ticker": ticker, "strike": assignment_price,
                        "adj_cost_basis": adj_cost_basis,
                    })
                    ctx = self._market_context
                    td  = ctx.get("tickers", {}).get(ticker, {})
                    log_decision_assignment(
                        ticker=ticker,
                        strike=assignment_price,
                        total_csp_premiums=total_premiums,
                        adj_cost_basis=adj_cost_basis,
                        stock_price_at_assignment=td.get("price", assignment_price),
                        iv_rank=td.get("iv_rank", 0),
                        vix=ctx.get("vix", 0),
                    )

            elif activity_type in ("OPEXP", "OPEXC"):
                logger.info(f"EXPIRY/EXERCISE detected: {symbol} for {ticker}")
                pos = self.state["positions"].get(ticker)
                if pos and pos["stage"] == "CSP":
                    # Put expired worthless — good!
                    premium = pos["premium_received"]
                    ctx = self._market_context
                    td  = ctx.get("tickers", {}).get(ticker, {})
                    log_decision_expiry(
                        ticker=ticker, stage="CSP",
                        contract=pos["contract"],
                        entry_premium=premium,
                        strike=pos["strike"],
                        stock_price=td.get("price", 0),
                        iv_rank=td.get("iv_rank", 0),
                        vix=ctx.get("vix", 0),
                    )
                    self._close_cycle(ticker, pos, pnl=premium * 100, reason="expired_worthless")

    def _check_profit_targets(self):
        """Check all open positions for 40%/50%/65% profit targets."""
        ctx = self._market_context
        if not ctx:
            return

        for ticker in TICKERS:
            pos = self.state["positions"].get(ticker)
            if not pos or not pos.get("contract"):
                continue

            vix_zone = ctx["vix_zone"]
            snapshot = get_current_option_snapshot(self.client, pos["contract"], ticker)
            if not snapshot:
                continue

            current_val = snapshot["mid"]
            entry_prem  = pos["premium_received"]
            profit_pct  = check_profit_pct(entry_prem, current_val)
            dte         = (date.fromisoformat(pos["expiration"]) - date.today()).days

            logger.info(f"{ticker} {pos['stage']} P&L: {profit_pct:.1%} (DTE={dte})")

            # ── Analytics: snapshot every position check ──────────────────────
            td = ctx["tickers"][ticker]
            rescue_mode = (
                pos["stage"] == "CC"
                and pos.get("assignment_price")
                and td["price"] < pos["assignment_price"] * RESCUE_TRIGGER_PCT
            )
            log_position_snapshot(
                ticker=ticker,
                task=self._current_task,
                stage=pos["stage"],
                contract=pos["contract"],
                strike=pos.get("strike", 0),
                expiration=pos["expiration"],
                dte=dte,
                entry_premium=entry_prem,
                current_mid=current_val,
                profit_pct=profit_pct,
                delta_now=snapshot.get("delta", 0),
                iv_now=snapshot.get("iv", 0),
                stock_price=td["price"],
                vix=ctx["vix"],
                rescue_mode=rescue_mode,
            )

            should_close = False
            reason = ""

            if profit_pct >= PROFIT_TAKE_GAMMA and dte < GAMMA_DTE_THRESHOLD:
                should_close = True
                reason = f"gamma_risk_{profit_pct:.0%}"

            elif profit_pct >= PROFIT_TAKE_50PCT:
                should_close = True
                reason = f"profit_{profit_pct:.0%}"

            elif profit_pct >= 0.40 and dte > PROFIT_TAKE_40PCT_DTE[ticker]:
                should_close = True
                reason = f"early_profit_{profit_pct:.0%}"

            if should_close:
                self._close_position(ticker, pos, snapshot, reason, profit_pct)

    def _evaluate_new_entries(self):
        """Determine if we should open a new CSP on any ticker."""
        ctx      = self._market_context
        if not ctx:
            return

        vix_zone  = ctx["vix_zone"]
        vix_params = ctx["vix_params"]
        account   = ctx["account"]
        earnings  = ctx["earnings"]

        # Sector stress freeze
        if self._is_sector_stress_frozen():
            logger.info("Sector stress freeze active — skipping new entries")
            return

        # Max positions check
        open_positions = count_open_positions(self.state)
        if open_positions >= vix_params["max_positions"]:
            logger.info(f"Max positions reached ({open_positions}/{vix_params['max_positions']})")
            return

        # Max 1 CSP at a time
        if count_open_csps(self.state) >= 1:
            logger.info("CSP already open — skipping new CSP")
            return

        # Total capital cap
        equity = account["equity"]
        deployed = self._get_deployed_capital()
        if equity > 0 and deployed / equity > MAX_TOTAL_DEPLOYED_PCT:
            logger.info(f"Capital limit reached: {deployed/equity:.1%} > {MAX_TOTAL_DEPLOYED_PCT:.0%}")
            return

        # Rank eligible tickers and pick best
        # Allow a new CSP if the other ticker is in CC stage (holding shares, no open put)
        eligible = []
        for ticker in TICKERS:
            pos = self.state["positions"].get(ticker)
            if pos:
                if pos["stage"] == "CC":
                    pass  # CC stage = holding shares, still eligible to open CSP on this ticker
                else:
                    continue  # CSP already open on this ticker

            td = ctx["tickers"][ticker]
            reason, is_conservative = self._csp_entry_check(ticker, td, vix_zone, vix_params, account, earnings)
            if reason:
                logger.info(f"{ticker} CSP BLOCKED: {reason}")
                log_decision_skip(
                    ticker=ticker, stage="CSP", reason=reason,
                    details={"iv_rank": td["iv_rank"], "rsi": td["rsi"], "price": td["price"]},
                    vix=ctx["vix"], vix_zone=vix_zone,
                )
                continue

            eligible.append((ticker, td["iv_rank"], td["rsi"], is_conservative))

        if not eligible:
            logger.info("No eligible tickers for new CSP")
            return

        # Ticker priority: higher IV Rank → better RSI position → PLTR tiebreaker
        eligible.sort(key=lambda x: (-x[1], abs(x[2] - 55), 0 if x[0] == "PLTR" else 1))

        simulated_cash = account["cash"]

        for item in eligible:
            chosen_ticker = item[0]
            chosen_conservative = item[3]
            
            # Re-check basic capital requirement with simulated cash
            td = ctx["tickers"][chosen_ticker]
            required = td["price"] * 100 * 0.80
            if simulated_cash < required:
                logger.info(f"{chosen_ticker} skipped during multi-entry: insufficient simulated cash (need ~${required:,.0f}, have ${simulated_cash:,.0f})")
                continue

            logger.info(f"Attempting new CSP for: {chosen_ticker} (conservative={chosen_conservative})")
            
            # Temporarily inject simulated_cash to avoid failing _open_csp checks inside
            original_cash = self._market_context["account"]["cash"]
            self._market_context["account"]["cash"] = simulated_cash
            
            success, required_capital = self._open_csp(chosen_ticker, vix_zone, vix_params, earnings_conservative=chosen_conservative)
            
            # Restore original cash in context
            self._market_context["account"]["cash"] = original_cash
            
            if success and required_capital:
                simulated_cash -= required_capital
                logger.info(f"Successfully opened CSP for {chosen_ticker}. Simulated cash remaining: ${simulated_cash:,.0f}")

    def _open_csp(self, ticker: str, vix_zone: str, vix_params: dict, earnings_conservative: bool = False) -> tuple[bool, float]:
        """Find and sell the best CSP for the given ticker."""
        td = self._market_context["tickers"][ticker]
        max_dte = min(CSP_MAX_DTE[ticker], vix_params["max_dte"])

        contracts = self.client.get_option_chain(
            underlying=ticker,
            option_type="put",
            dte_min=3,
            dte_max=max_dte,
        )
        best = find_best_put(contracts, ticker, td["iv_rank"], vix_zone, max_dte, earnings_conservative=earnings_conservative)
        if not best:
            logger.warning(f"{ticker} CSP: no contract found")
            return False, 0.0

        # Capital check
        required_capital = best["strike"] * 100
        account = self._market_context["account"]
        if required_capital > account["cash"]:
            logger.warning(f"{ticker} insufficient cash: need ${required_capital:,.0f}, have ${account['cash']:,.0f}")
            return False, 0.0

        result = sell_option(self.client, best, ticker)
        if not result:
            logger.error(f"{ticker} CSP sell order failed")
            return False, 0.0

        position = blank_position(
            ticker=ticker,
            stage="CSP",
            contract_symbol=best["symbol"],
            strike=best["strike"],
            expiration=best["expiration"],
            dte=best["dte"],
            premium=result["filled_price"],
            delta=best["delta"],
            iv_rank=td["iv_rank"],
            vix=self._market_context["vix"],
        )
        position["open_order_id"] = result["order_id"]
        self.state["positions"][ticker] = position
        self.state["last_entry_date"][ticker] = date.today().isoformat()

        log_action(self.state, "OPEN_CSP", {
            "ticker":    ticker,
            "contract":  best["symbol"],
            "strike":    best["strike"],
            "expiration": best["expiration"],
            "premium":   result["filled_price"],
            "delta":     best["delta"],
            "iv_rank":   td["iv_rank"],
            "vix":       self._market_context["vix"],
        })

        # ── Analytics ─────────────────────────────────────────────────────────
        stock_price = td["price"]
        otm_pct = (stock_price - best["strike"]) / stock_price if stock_price else 0
        ann_ret = (result["filled_price"] / (best["strike"] * 100)) * (365 / best["dte"]) * 100
        log_decision_open(
            ticker=ticker, stage="CSP",
            contract=best["symbol"], strike=best["strike"],
            expiration=best["expiration"], dte=best["dte"],
            delta=best["delta"], premium=result["filled_price"],
            iv=best.get("iv", td["iv"]), iv_rank=td["iv_rank"],
            vix=self._market_context["vix"], vix_zone=vix_zone,
            rsi=td["rsi"], stock_price=stock_price,
            otm_pct=otm_pct, ann_return_pct=ann_ret,
        )
        return True, required_capital

    def _open_cc(self, ticker: str):
        """Sell a covered call on assigned shares."""
        pos = self.state["positions"].get(ticker)
        if not pos or pos.get("stage") != "CC":
            return
        if pos.get("contract"):
            logger.info(f"{ticker} CC already open")
            return

        ctx       = self._market_context
        td        = ctx["tickers"][ticker]
        vix_zone  = ctx["vix_zone"]
        vix_params = ctx["vix_params"]
        max_dte   = min(CC_MAX_DTE[ticker], vix_params["max_dte"])

        adj_cost_basis = pos["adjusted_cost_basis"]
        stock_price    = td["price"]
        rescue_mode    = stock_price < (pos["assignment_price"] * RESCUE_TRIGGER_PCT)

        if rescue_mode:
            logger.info(f"{ticker} RESCUE MODE active (price={stock_price:.2f} < {pos['assignment_price'] * RESCUE_TRIGGER_PCT:.2f})")
            # Circuit breaker check
            loss_pct = (stock_price - pos["assignment_price"]) / pos["assignment_price"]
            if loss_pct <= CIRCUIT_BREAKER[ticker]:
                logger.warning(f"{ticker} CIRCUIT BREAKER triggered ({loss_pct:.1%}) — stopping all activity")
                log_action(self.state, "CIRCUIT_BREAKER", {"ticker": ticker, "loss_pct": loss_pct})
                log_circuit_breaker(ticker=ticker, loss_pct=loss_pct, vix=ctx["vix"])
                return

            # Pause CC if TSLA > 20% down and VIX > 30
            if ticker == "TSLA" and loss_pct < -0.20 and ctx["vix"] > 30:
                logger.info("TSLA rescue: pausing CC — drop > 20% + VIX > 30")
                return

        earn_str = ctx["earnings"].get(ticker)
        earnings_conservative = False
        if earn_str:
            earn_date = date.fromisoformat(earn_str[:10])
            max_exp = date.today() + timedelta(days=max_dte)
            if (earn_date - max_exp).days <= EARNINGS_BLACKOUT_DAYS:
                earnings_conservative = True

        contracts = self.client.get_option_chain(
            underlying=ticker,
            option_type="call",
            dte_min=3,
            dte_max=max_dte,
        )
        best = find_best_call(
            contracts=contracts,
            ticker=ticker,
            iv_rank=td["iv_rank"],
            vix_zone=vix_zone,
            cost_basis=pos["assignment_price"],
            adjusted_cost_basis=adj_cost_basis,
            max_dte=max_dte,
            rescue_mode=rescue_mode,
            earnings_conservative=earnings_conservative,
        )
        if not best:
            logger.warning(f"{ticker} CC: no contract found")
            return

        result = sell_option(self.client, best, ticker)
        if not result:
            logger.error(f"{ticker} CC sell order failed")
            return

        pos["contract"]   = best["symbol"]
        pos["expiration"] = best["expiration"]
        pos["dte_at_entry"] = best["dte"]
        pos["premium_received"] = result["filled_price"]
        pos["total_premiums_this_lot"] += result["filled_price"]
        pos["adjusted_cost_basis"] = pos["assignment_price"] - pos["total_premiums_this_lot"] / 100

        log_action(self.state, "OPEN_CC", {
            "ticker":        ticker,
            "contract":      best["symbol"],
            "strike":        best["strike"],
            "expiration":    best["expiration"],
            "premium":       result["filled_price"],
            "rescue_mode":   rescue_mode,
            "adj_cost_basis": pos["adjusted_cost_basis"],
        })

        # ── Analytics ─────────────────────────────────────────────────────────
        otm_pct = (best["strike"] - td["price"]) / td["price"] if td["price"] else 0
        ann_ret = (result["filled_price"] / (best["strike"] * 100)) * (365 / best["dte"]) * 100
        log_decision_open(
            ticker=ticker, stage="CC",
            contract=best["symbol"], strike=best["strike"],
            expiration=best["expiration"], dte=best["dte"],
            delta=best["delta"], premium=result["filled_price"],
            iv=best.get("iv", td["iv"]), iv_rank=td["iv_rank"],
            vix=ctx["vix"], vix_zone=vix_zone,
            rsi=td["rsi"], stock_price=td["price"],
            otm_pct=otm_pct, ann_return_pct=ann_ret,
        )

    def _close_position(self, ticker: str, pos: dict, snapshot: dict, reason: str, profit_pct: float):
        """Buy to close an open option position."""
        result = buy_to_close(self.client, snapshot, ticker)
        if not result:
            logger.error(f"{ticker} buy-to-close failed for reason={reason}")
            return

        cost_to_close  = result["filled_price"]
        net_premium    = pos["premium_received"] - cost_to_close
        realized_pnl   = net_premium * 100

        log_action(self.state, "CLOSE_OPTION", {
            "ticker":        ticker,
            "stage":         pos["stage"],
            "contract":      pos["contract"],
            "entry_premium": pos["premium_received"],
            "close_price":   cost_to_close,
            "net_premium":   net_premium,
            "realized_pnl":  realized_pnl,
            "reason":        reason,
        })

        # ── Analytics ─────────────────────────────────────────────────────────
        ctx = self._market_context
        td  = ctx.get("tickers", {}).get(ticker, {})
        dte_rem = (date.fromisoformat(pos["expiration"]) - date.today()).days
        log_decision_close(
            ticker=ticker, stage=pos["stage"],
            contract=pos["contract"],
            entry_premium=pos["premium_received"],
            close_price=cost_to_close,
            profit_pct=profit_pct,
            dte_remaining=dte_rem,
            reason=reason,
            stock_price=td.get("price", 0),
            iv_rank=td.get("iv_rank", 0),
            vix=ctx.get("vix", 0),
        )

        if pos["stage"] == "CSP":
            self._close_cycle(ticker, pos, pnl=realized_pnl, reason=reason)
        elif pos["stage"] == "CC":
            # CC closed, still own shares — update position
            pos["contract"] = None
            pos["total_premiums_this_lot"] += net_premium
            pos["adjusted_cost_basis"] = (
                pos["assignment_price"] - pos["total_premiums_this_lot"] / 100
            )
            # Immediately look for next CC
            self._open_cc(ticker)

    def _close_cycle(self, ticker: str, pos: dict, pnl: float, reason: str):
        """Complete a CSP cycle (expired worthless or closed)."""
        perf = self.state["performance"][ticker]
        perf["cycles"]        += 1
        perf["total_premium"] += pos["premium_received"] * 100
        perf["realized_pnl"]  += pnl
        if pnl >= 0:
            perf["wins"]   += 1
        else:
            perf["losses"] += 1

        self.state["positions"][ticker] = None
        self.state["last_exit_date"][ticker] = date.today().isoformat()

        log_action(self.state, "CLOSE_CYCLE", {
            "ticker": ticker, "pnl": pnl, "reason": reason,
        })

    def _evaluate_rolling(self):
        """Check if any position should be rolled."""
        ctx = self._market_context
        if not ctx:
            return

        for ticker in TICKERS:
            pos = self.state["positions"].get(ticker)
            if not pos or not pos.get("contract"):
                continue

            stage = pos["stage"]
            stock_price = ctx["tickers"][ticker]["price"]
            strike = pos["strike"]
            dte = (date.fromisoformat(pos["expiration"]) - date.today()).days

            if dte <= 5:  # too close to expiry to roll
                continue
            if pos.get("rolls_count", 0) >= MAX_ROLLS[ticker]:
                continue

            snapshot = get_current_option_snapshot(self.client, pos["contract"], ticker)
            if not snapshot:
                continue

            should_roll = False
            roll_direction = None
            drop_trigger  = ROLL_TRIGGER_DROP_PCT[ticker]
            surge_trigger = ROLL_TRIGGER_SURGE_PCT[ticker]

            if stage == "CSP":
                price_drop = (strike - stock_price) / strike
                if price_drop > drop_trigger and snapshot.get("delta", 0) < -0.50:
                    should_roll = True
                    roll_direction = "down"

            elif stage == "CC":
                price_surge = (stock_price - strike) / strike
                if price_surge > surge_trigger and snapshot.get("delta", 0) > 0.50:
                    should_roll = True
                    roll_direction = "up"

            if not should_roll:
                continue

            logger.info(f"{ticker} {stage} roll candidate: direction={roll_direction}")
            self._execute_roll(ticker, pos, snapshot, roll_direction)

    def _execute_roll(self, ticker: str, pos: dict, current_snapshot: dict, direction: str):
        """Close existing and open new rolled contract."""
        ctx       = self._market_context
        vix_zone  = ctx["vix_zone"]
        vix_params = ctx["vix_params"]

        stage    = pos["stage"]
        opt_type = "put" if stage == "CSP" else "call"
        max_dte  = min(
            (CSP_MAX_DTE if stage == "CSP" else CC_MAX_DTE)[ticker],
            vix_params["max_dte"]
        )
        # Roll to a further-out expiry (add 1 week)
        dte_min = max(5, (date.fromisoformat(pos["expiration"]) - date.today()).days + 5)
        dte_max = dte_min + 7

        earn_str = self.state["earnings"].get(ticker)
        earnings_conservative = False
        if earn_str:
            earn_date = date.fromisoformat(earn_str[:10])
            max_exp = date.today() + timedelta(days=dte_max)
            if (earn_date - max_exp).days <= EARNINGS_BLACKOUT_DAYS:
                earnings_conservative = True

        contracts = self.client.get_option_chain(
            underlying=ticker, option_type=opt_type,
            dte_min=dte_min, dte_max=min(dte_max, max_dte),
        )

        td = ctx["tickers"][ticker]
        if stage == "CSP":
            new_contract = find_best_put(contracts, ticker, td["iv_rank"], vix_zone, dte_max, earnings_conservative=earnings_conservative)
        else:
            new_contract = find_best_call(
                contracts=contracts, ticker=ticker, iv_rank=td["iv_rank"],
                vix_zone=vix_zone, cost_basis=pos.get("assignment_price", 0),
                adjusted_cost_basis=pos.get("adjusted_cost_basis", 0), max_dte=dte_max,
                earnings_conservative=earnings_conservative,
            )

        if not new_contract:
            logger.warning(f"{ticker} roll: no new contract found")
            return

        if not evaluate_roll_candidate(current_snapshot, new_contract, stage):
            logger.info(f"{ticker} roll rejected: net debit — never roll for debit")
            return

        # Execute: close old, open new
        close_result = buy_to_close(self.client, current_snapshot, ticker)
        if not close_result:
            return

        open_result = sell_option(self.client, new_contract, ticker)
        if not open_result:
            logger.error(f"{ticker} roll: sell failed after close — position may be uncovered!")
            return

        net_credit = open_result["filled_price"] - close_result["filled_price"]
        pos["contract"]   = new_contract["symbol"]
        pos["expiration"] = new_contract["expiration"]
        pos["premium_received"] = open_result["filled_price"]
        pos["total_premiums_this_lot"] += net_credit
        pos["rolls_count"] += 1

        log_action(self.state, "ROLL", {
            "ticker": ticker, "stage": stage,
            "direction": direction,
            "new_contract": new_contract["symbol"],
            "net_credit": net_credit,
            "rolls_count": pos["rolls_count"],
        })

        # ── Analytics ─────────────────────────────────────────────────────────
        log_decision_roll(
            ticker=ticker, stage=stage,
            old_contract=current_snapshot["symbol"],
            new_contract=new_contract["symbol"],
            old_strike=pos.get("strike", 0),
            new_strike=new_contract["strike"],
            old_expiration=pos.get("expiration", ""),
            new_expiration=new_contract["expiration"],
            net_credit=net_credit,
            direction=direction,
            rolls_count=pos["rolls_count"],
            stock_price=ctx["tickers"][ticker]["price"],
            vix=ctx["vix"],
        )

    def _check_earnings_proximity(self):
        """Close/flag positions approaching earnings blackout."""
        ctx = self._market_context
        if not ctx:
            return

        for ticker in TICKERS:
            pos = self.state["positions"].get(ticker)
            if not pos or not pos.get("contract"):
                continue

            earn_str = ctx["earnings"].get(ticker)
            if not earn_str:
                continue

            earn_date = date.fromisoformat(earn_str[:10])
            exp_date  = date.fromisoformat(pos["expiration"])
            days_to_earnings_from_exp = (earn_date - exp_date).days

            if days_to_earnings_from_exp <= EARNINGS_BLACKOUT_DAYS:
                snapshot = get_current_option_snapshot(self.client, pos["contract"], ticker)
                if not snapshot:
                    continue

                entry_prem = pos["premium_received"]
                profit_pct = check_profit_pct(entry_prem, snapshot["mid"])

                if profit_pct >= EARNINGS_CONSERVATIVE_PROFIT_TAKE:
                    logger.info(f"{ticker} earnings proximity — closing at {profit_pct:.0%} profit")
                    self._close_position(ticker, pos, snapshot, "earnings_blackout", profit_pct)
                else:
                    logger.warning(
                        f"{ticker} earnings proximity WARNING: position {pos['contract']} "
                        f"expires near earnings {earn_str}, only {profit_pct:.0%} profit"
                    )

    def _check_sector_stress(self):
        """Update sector stress freeze if QQQ dropped > 3%."""
        from datetime import datetime
        qqq_chg = self._market_context.get("qqq_change", 0.0)
        if qqq_chg <= -QQQ_DROP_FREEZE_PCT:
            freeze_until = (datetime.utcnow() + timedelta(hours=SECTOR_FREEZE_HOURS)).isoformat()
            self.state["sector_stress_freeze_until"] = freeze_until
            logger.warning(f"SECTOR STRESS: QQQ {qqq_chg:.2%} — freezing new CSPs until {freeze_until}")
            log_action(self.state, "SECTOR_STRESS_FREEZE", {"qqq_change": qqq_chg, "until": freeze_until})

        # Check CC stage tickers — open CC if position is in CC stage with no open contract
        for ticker in TICKERS:
            pos = self.state["positions"].get(ticker)
            if pos and pos["stage"] == "CC" and not pos.get("contract"):
                self._open_cc(ticker)

    def _is_sector_stress_frozen(self) -> bool:
        from datetime import datetime
        freeze_until = self.state.get("sector_stress_freeze_until")
        if not freeze_until:
            return False
        return datetime.utcnow().isoformat() < freeze_until

    def _confirm_fills(self):
        """Check if any pending open orders have filled."""
        for ticker in TICKERS:
            pos = self.state["positions"].get(ticker)
            if not pos:
                continue
            order_id = pos.get("open_order_id")
            if not order_id:
                continue
            order = self.client.get_order(order_id)
            if order["status"] == "filled" and not pos.get("fill_confirmed"):
                pos["fill_confirmed"] = True
                pos["premium_received"] = order["filled_price"] or pos["premium_received"]
                logger.info(f"{ticker} order {order_id} confirmed filled @ ${pos['premium_received']:.2f}")

    def _cancel_unfilled_orders(self):
        """Cancel any unfilled day orders at pre-close."""
        open_orders = self.client.get_open_orders()
        for order in open_orders:
            if order["status"] in ("accepted", "pending_new", "new", "held"):
                logger.info(f"Pre-close: cancelling unfilled order {order['id']} ({order['symbol']})")
                self.client.cancel_order(order["id"])

    def _csp_entry_check(
        self, ticker: str, td: dict, vix_zone: str, vix_params: dict, account: dict, earnings: dict
    ) -> tuple[Optional[str], bool]:
        """
        Return (block_reason, earnings_conservative_flag).
        If block_reason is not None, CSP is blocked.
        """
        earnings_conservative = False

        # 1. Capital
        required = td["price"] * 100 * 0.80  # rough estimate
        if account["cash"] < required:
            return f"insufficient_cash (need ~${required:,.0f})", False

        # 2. Earnings blackout
        earn_str = earnings.get(ticker)
        if earn_str:
            earn_date = date.fromisoformat(earn_str[:10])
            # Use max DTE for blackout check
            max_exp = date.today() + timedelta(days=CSP_MAX_DTE[ticker])
            days_to_earn = (earn_date - date.today()).days
            if (earn_date - max_exp).days <= EARNINGS_BLACKOUT_DAYS:
                if -POST_EARNINGS_WAIT <= days_to_earn <= 0:
                    return f"post_earnings_wait ({earn_str})", False
                else:
                    logger.info(f"{ticker} within {EARNINGS_BLACKOUT_DAYS}d of earnings max_exp: engaging conservative mode")
                    earnings_conservative = True

        # 3. VIX zone position limit already enforced before calling this

        # 4. IV Rank
        iv_rank = td["iv_rank"]
        if iv_rank < IV_RANK_LOW_ZONE:
            return f"iv_rank_too_low ({iv_rank:.1%})", False
        if iv_rank < IV_RANK_MIN_ENTRY and vix_zone != "low":
            return f"iv_rank_below_threshold ({iv_rank:.1%}) in {vix_zone} VIX zone", False

        # 5. Concentration — only 1 CSP at a time (enforced in caller)

        # 6. RSI
        rsi = td["rsi"]
        if not (RSI_MIN <= rsi <= RSI_MAX):
            return f"rsi_out_of_range ({rsi:.1f})", False

        # 7. Cooldown
        last_exit = self.state["last_exit_date"].get(ticker)
        if last_exit:
            days_since_exit = (date.today() - date.fromisoformat(last_exit)).days
            if days_since_exit < CSP_REENTRY_COOLDOWN_SESSIONS:
                return f"cooldown ({days_since_exit} days since exit)", False

        return None, earnings_conservative

    def _get_deployed_capital(self) -> float:
        """Estimate capital currently deployed in open positions."""
        total = 0.0
        for ticker in TICKERS:
            pos = self.state["positions"].get(ticker)
            if pos:
                if pos["stage"] == "CSP" and pos.get("strike"):
                    total += pos["strike"] * 100
                elif pos["stage"] == "CC" and pos.get("assignment_price"):
                    total += pos["assignment_price"] * 100
        return total

    def _generate_daily_summary(self):
        """Print and log the daily summary report."""
        from bot.reporter import generate_daily_report
        ctx = self._market_context or {}
        report = generate_daily_report(self.state, ctx)
        print(report)
        logger.info("\n" + report)

        # Save report to logs dir
        from bot.config import LOG_DIR
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        report_file = LOG_DIR / f"daily_{date.today().isoformat()}.txt"
        with open(report_file, "w") as f:
            f.write(report)
