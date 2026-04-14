"""
Market data helpers: VIX, RSI, earnings calendar, QQQ daily change.
Uses yfinance for free market data.
"""
import logging
from datetime import date, timedelta
from typing import Optional

import yfinance as yf
import numpy as np

from bot.config import VIX_ZONES, VIX_ZONE_PARAMS

logger = logging.getLogger(__name__)


def get_vix() -> float:
    """Fetch current VIX index level."""
    try:
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"VIX fetch failed: {e}")
    return 20.0  # fallback to neutral


def get_vix_zone(vix: float) -> str:
    for zone, (lo, hi) in VIX_ZONES.items():
        if lo <= vix < hi:
            return zone
    return "extreme"


def get_vix_zone_params(vix_zone: str) -> dict:
    max_cap, max_pos, max_dte, delta_shift = VIX_ZONE_PARAMS[vix_zone]
    return {
        "max_capital_pct": max_cap,
        "max_positions":   max_pos,
        "max_dte":         max_dte,
        "delta_otm_shift": delta_shift,
    }


def calculate_rsi(closes: list[float], period: int = 14) -> float:
    """Wilder's RSI."""
    if len(closes) < period + 1:
        return 50.0  # neutral fallback
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def get_rsi(ticker: str, period: int = 14) -> float:
    """Fetch RSI(14) for a ticker using yfinance."""
    try:
        hist = yf.Ticker(ticker).history(period="3mo")
        if len(hist) < period + 5:
            return 50.0
        closes = hist["Close"].tolist()
        return calculate_rsi(closes, period)
    except Exception as e:
        logger.warning(f"RSI fetch failed for {ticker}: {e}")
        return 50.0


def get_current_iv(ticker: str) -> float:
    """
    Approximate current 30-day IV from ATM options via yfinance.
    Falls back to 0.50 if unavailable.
    """
    try:
        tk = yf.Ticker(ticker)

        # Safely get spot price
        spot = None
        try:
            spot = float(tk.fast_info.last_price)
        except Exception:
            pass
        if not spot:
            try:
                spot = float(tk.info.get("regularMarketPrice") or 0)
            except Exception:
                pass
        if not spot:
            return 0.50

        # Find nearest expiry ~30 days out
        exps = tk.options
        if not exps:
            return 0.50
        target = date.today() + timedelta(days=30)
        nearest = min(exps, key=lambda e: abs((date.fromisoformat(e) - target).days))

        # option_chain returns an OptionChain namedtuple with .calls and .puts
        chain = tk.option_chain(nearest)
        puts = getattr(chain, "puts", chain[1] if len(chain) > 1 else None)
        if puts is None or puts.empty:
            return 0.50

        puts = puts.copy()
        puts["dist"] = abs(puts["strike"] - spot)
        atm_put = puts.sort_values("dist").iloc[0]
        iv = float(atm_put.get("impliedVolatility", 0.50))
        return iv if iv > 0 else 0.50
    except Exception as e:
        logger.warning(f"IV fetch failed for {ticker}: {e}")
    return 0.50


def get_stock_price_yf(ticker: str) -> float:
    """Fallback stock price via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        try:
            price = float(tk.fast_info.last_price)
            if price and price > 0:
                return price
        except Exception:
            pass
        # Fallback: last bar close
        hist = tk.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return 0.0


def get_qqq_daily_change() -> float:
    """QQQ daily % change (negative = down)."""
    try:
        hist = yf.Ticker("QQQ").history(period="2d")
        if len(hist) >= 2:
            prev_close = hist["Close"].iloc[-2]
            last_close = hist["Close"].iloc[-1]
            return (last_close - prev_close) / prev_close
    except Exception as e:
        logger.warning(f"QQQ change fetch failed: {e}")
    return 0.0


def get_earnings_dates() -> dict:
    """
    Get next known earnings date for TSLA and PLTR.
    yfinance 0.2.40+: tk.calendar returns a dict like:
      {'Earnings Date': [Timestamp(...)], 'Revenue High': [...], ...}
    Returns {ticker: 'YYYY-MM-DD' or None}.
    """
    results = {}
    for ticker in ["TSLA", "PLTR"]:
        earn_str = None
        try:
            cal = yf.Ticker(ticker).calendar
            if cal and isinstance(cal, dict):
                # Key may be 'Earnings Date' or similar
                for key in ("Earnings Date", "earningsDate", "earnings_date"):
                    earn_list = cal.get(key, [])
                    if earn_list:
                        earn_dt = earn_list[0]
                        if hasattr(earn_dt, "date"):
                            earn_dt = earn_dt.date()
                        earn_str = str(earn_dt)[:10]
                        break
            elif cal is not None and hasattr(cal, "columns"):
                # Older yfinance: DataFrame
                earnings_col = cal.columns[0]
                earn_dt = cal[earnings_col].iloc[0]
                if hasattr(earn_dt, "date"):
                    earn_dt = earn_dt.date()
                earn_str = str(earn_dt)[:10]
        except Exception as e:
            logger.warning(f"Earnings fetch failed for {ticker}: {e}")
        results[ticker] = earn_str
    return results


def days_until_earnings(ticker: str, earnings_dates: dict) -> Optional[int]:
    """Days from today until earnings. None if unknown."""
    earn_str = earnings_dates.get(ticker)
    if not earn_str:
        return None
    try:
        earn_date = date.fromisoformat(earn_str[:10])
        return (earn_date - date.today()).days
    except Exception:
        return None


def is_market_open() -> bool:
    """Simple check: Mon-Fri, not checking holidays."""
    from datetime import datetime
    import pytz
    et = pytz.timezone("America/New_York")
    now = datetime.now(et)
    if now.weekday() >= 5:  # Sat, Sun
        return False
    market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now <= market_close
