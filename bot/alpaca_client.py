"""
Alpaca API wrapper — trading + market data for options and stocks.
"""
import logging
from datetime import date, timedelta
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
)
from alpaca.trading.enums import (
    OrderSide, TimeInForce, OrderType, QueryOrderStatus,
    ContractType,
)
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    OptionChainRequest,
    StockBarsRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame

from bot.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, PAPER_TRADING

logger = logging.getLogger(__name__)


class AlpacaClient:
    def __init__(self):
        self.trading = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=PAPER_TRADING,
        )
        self.option_data = OptionHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
        self.stock_data = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )

    # ── Account ────────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        acc = self.trading.get_account()
        return {
            "equity":          float(acc.equity),
            "cash":            float(acc.cash),
            "buying_power":    float(acc.buying_power),
            "portfolio_value": float(acc.portfolio_value),
        }

    def get_positions(self) -> list[dict]:
        positions = self.trading.get_all_positions()
        result = []
        for p in positions:
            result.append({
                "symbol":    p.symbol,
                "qty":       float(p.qty),
                "side":      p.side.value,
                "avg_price": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "asset_class": p.asset_class.value if p.asset_class else None,
            })
        return result

    def get_open_orders(self) -> list[dict]:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = self.trading.get_orders(req)
        return [self._order_to_dict(o) for o in orders]

    def get_order(self, order_id: str) -> dict:
        order = self.trading.get_order_by_id(order_id)
        return self._order_to_dict(order)

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.trading.cancel_order_by_id(order_id)
            logger.info(f"Cancelled order {order_id}")
            return True
        except Exception as e:
            logger.error(f"Cancel order {order_id} failed: {e}")
            return False

    def get_activities(self, activity_types: list[str]) -> list[dict]:
        """Poll for assignment/expiry events."""
        try:
            activities = self.trading.get_portfolio_history()  # fallback
        except Exception:
            activities = []
        # Direct activities endpoint
        try:
            import requests, json as _json
            from bot.config import ALPACA_BASE_URL
            headers = {
                "APCA-API-KEY-ID": ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            }
            params = {"activity_types": ",".join(activity_types)}
            r = requests.get(
                f"{ALPACA_BASE_URL}/v2/account/activities",
                headers=headers,
                params=params,
                timeout=10,
            )
            if r.ok:
                return r.json()
        except Exception as e:
            logger.warning(f"Activities fetch failed: {e}")
        return []

    # ── Options ────────────────────────────────────────────────────────────────

    def get_option_chain(
        self,
        underlying: str,
        option_type: str,            # "put" or "call"
        dte_min: int,
        dte_max: int,
        strike_pct_range: float = 0.20,   # ±% from current price
    ) -> list[dict]:
        """
        Fetch the options chain for an underlying, filtered by DTE and strike range.
        Returns a list of contract snapshots with greeks.
        """
        today = date.today()
        exp_from = today + timedelta(days=dte_min)
        exp_to   = today + timedelta(days=dte_max)

        try:
            current_price = self.get_stock_price(underlying)
        except Exception:
            current_price = None

        req = OptionChainRequest(
            underlying_symbol=underlying,
            expiration_date_gte=exp_from,
            expiration_date_lte=exp_to,
            type=ContractType.PUT if option_type == "put" else ContractType.CALL,
        )

        try:
            chain = self.option_data.get_option_chain(req)
        except Exception as e:
            logger.error(f"Option chain fetch failed for {underlying}: {e}")
            return []

        results = []
        for symbol, snapshot in chain.items():
            try:
                greeks = snapshot.greeks
                quote  = snapshot.latest_quote
                trade  = snapshot.latest_trade

                if greeks is None or quote is None:
                    continue

                bid   = float(quote.bid_price or 0)
                ask   = float(quote.ask_price or 0)
                mid   = round((bid + ask) / 2, 2)
                spread = round(ask - bid, 2)

                # Parse contract symbol to extract strike and expiry
                details = self._parse_option_symbol(symbol)
                if not details:
                    continue

                exp_date = details["expiration"]
                dte = (exp_date - today).days
                if dte < dte_min or dte > dte_max:
                    continue

                # Strike range filter
                if current_price:
                    strike = details["strike"]
                    if abs(strike - current_price) / current_price > strike_pct_range:
                        continue

                results.append({
                    "symbol":      symbol,
                    "underlying":  underlying,
                    "type":        option_type,
                    "strike":      details["strike"],
                    "expiration":  exp_date.isoformat(),
                    "dte":         dte,
                    "delta":       float(greeks.delta or 0),
                    "gamma":       float(greeks.gamma or 0),
                    "theta":       float(greeks.theta or 0),
                    "vega":        float(greeks.vega or 0),
                    "iv":          float(snapshot.implied_volatility or 0),
                    "bid":         bid,
                    "ask":         ask,
                    "mid":         mid,
                    "spread":      spread,
                })
            except Exception as e:
                logger.debug(f"Skip contract {symbol}: {e}")
                continue

        return results

    # ── Stock Data ─────────────────────────────────────────────────────────────

    def get_stock_price(self, ticker: str) -> float:
        req = StockLatestTradeRequest(symbol_or_symbols=ticker)
        trade = self.stock_data.get_stock_latest_trade(req)
        return float(trade[ticker].price)

    def get_stock_bars(self, ticker: str, days: int = 90) -> list[dict]:
        """Return daily OHLCV bars for RSI calculation."""
        start = date.today() - timedelta(days=days + 10)
        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=start,
        )
        bars = self.stock_data.get_stock_bars(req)
        result = []
        for bar in bars[ticker]:
            result.append({
                "date":  bar.timestamp.date().isoformat(),
                "open":  float(bar.open),
                "high":  float(bar.high),
                "low":   float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume),
            })
        return result

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_limit_order(
        self,
        symbol: str,
        qty: int,
        side: str,            # "buy" or "sell"
        limit_price: float,
        time_in_force: str = "day",
    ) -> Optional[str]:
        """Place a limit order. Returns order_id on success."""
        try:
            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                type=OrderType.LIMIT,
                limit_price=round(limit_price, 2),
                time_in_force=TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC,
            )
            order = self.trading.submit_order(req)
            logger.info(f"Placed {side} limit order for {symbol} @ ${limit_price:.2f} → id={order.id}")
            return str(order.id)
        except Exception as e:
            logger.error(f"place_limit_order failed for {symbol}: {e}")
            return None

    def replace_order_price(self, order_id: str, new_limit_price: float) -> Optional[str]:
        """Replace an unfilled order with a new limit price."""
        try:
            import requests
            from bot.config import ALPACA_BASE_URL
            headers = {
                "APCA-API-KEY-ID": ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
                "Content-Type": "application/json",
            }
            payload = {"limit_price": str(round(new_limit_price, 2))}
            r = requests.patch(
                f"{ALPACA_BASE_URL}/v2/orders/{order_id}",
                headers=headers,
                json=payload,
                timeout=10,
            )
            if r.ok:
                new_id = r.json().get("id", order_id)
                logger.info(f"Replaced order {order_id} → {new_id} @ ${new_limit_price:.2f}")
                return new_id
            else:
                logger.warning(f"Replace order failed: {r.text}")
                return None
        except Exception as e:
            logger.error(f"replace_order_price failed: {e}")
            return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _order_to_dict(self, o) -> dict:
        return {
            "id":           str(o.id),
            "symbol":       o.symbol,
            "side":         o.side.value,
            "qty":          float(o.qty or 0),
            "filled_qty":   float(o.filled_qty or 0),
            "limit_price":  float(o.limit_price) if o.limit_price else None,
            "filled_price": float(o.filled_avg_price) if o.filled_avg_price else None,
            "status":       o.status.value,
            "created_at":   str(o.created_at),
        }

    @staticmethod
    def _parse_option_symbol(symbol: str) -> Optional[dict]:
        """
        Parse OCC option symbol: e.g. TSLA250418P00300000
        → {underlying, expiration: date, type, strike: float}
        """
        try:
            # Find where the date starts (6 digits after ticker letters)
            i = 0
            while i < len(symbol) and symbol[i].isalpha():
                i += 1
            underlying = symbol[:i]
            date_str   = symbol[i:i+6]       # YYMMDD
            opt_type   = symbol[i+6]          # P or C
            strike_str = symbol[i+7:]         # 8 digits, last 3 are cents

            exp_date = date(2000 + int(date_str[:2]),
                            int(date_str[2:4]),
                            int(date_str[4:6]))
            strike   = int(strike_str) / 1000.0

            return {
                "underlying":  underlying,
                "expiration":  exp_date,
                "type":        "put" if opt_type == "P" else "call",
                "strike":      strike,
            }
        except Exception:
            return None
