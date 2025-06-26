# trend_strategy.py
"""
Trend‑following strategy module
--------------------------------
Implements the entry/exit logic described in the brief:

* Trend timeframe: 2‑hour candles, 200‑EMA slope & price relationship
* Trade timeframe: 15‑minute candles
* Entry criteria
    * **Uptrend**  – go **long** when the 15‑min close prints **below** its 200‑EMA
    * **Downtrend** – go **short** when the 15‑min close prints **above** its 200‑EMA
* Targets
    * **TP** = highest (long) / lowest (short) of the last 10 × 2‑hour bars
    * **SL** = opposite extreme of the same 10‑bar window

The object is completely stateless – it just needs a ``client`` that exposes a
``.get_klines(symbol, interval, limit)`` method (live Binance client OR an
offline stub in the back‑tester).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from utils.indicators import ema
from utils.time import now_utc


class TrendStrategy:
    """Pure‑logic strategy class that can be reused in live, paper, or back‑tests."""

    def __init__(
        self,
        client,
        symbol: str = "BTCUSDT",
        trend_tf: str = "2h",
        trade_tf: str = "15m",
        ema_period: int = 200,
        lookback: int = 10,
    ) -> None:
        self.client = client
        self.symbol = symbol
        self.trend_tf = trend_tf
        self.trade_tf = trade_tf
        self.ema_period = ema_period
        self.lookback = lookback

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _trend_direction(self, trend_candles: List[Dict]) -> str:
        closes = np.array([c["close"] for c in trend_candles], dtype=float)
        ema_series = ema(closes, self.ema_period)

        if closes[-1] > ema_series[-1] and ema_series[-1] > ema_series[-2]:
            return "up"
        if closes[-1] < ema_series[-1] and ema_series[-1] < ema_series[-2]:
            return "down"
        return "neutral"

    def _entry_signal(self, trade_candles: List[Dict], direction: str) -> Optional[str]:
        close = trade_candles[-1]["close"]
        ema_trade = ema([c["close"] for c in trade_candles], self.ema_period)[-1]

        if direction == "up" and close < ema_trade:
            return "long"
        if direction == "down" and close > ema_trade:
            return "short"
        return None

    def _targets(self, trend_candles: List[Dict], bias: str) -> Tuple[float, float]:
        highs = max(c["high"] for c in trend_candles[-self.lookback:])
        lows = min(c["low"] for c in trend_candles[-self.lookback:])

        return (highs, lows) if bias == "long" else (lows, highs)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def on_new_trade_candle(self) -> Optional[Dict]:
        """Call on every *closed* 15‑minute candle. Returns order dict or ``None``."""

        trend_candles = self.client.get_klines(
            self.symbol,
            self.trend_tf,
            limit=self.ema_period + self.lookback + 5,
        )
        trade_candles = self.client.get_klines(
            self.symbol,
            self.trade_tf,
            limit=self.ema_period + 5,
        )

        direction = self._trend_direction(trend_candles)
        if direction == "neutral":
            return None

        bias = self._entry_signal(trade_candles, direction)
        if not bias:
            return None

        tp, sl = self._targets(trend_candles, bias)

        # ───────── live order-book quote instead of 15-m close ─────────
        ticker = self.client.get_ticker(self.symbol)
        if bias == "long":
            entry_price = ticker["ask"]  # pay the ask to go long
        else:
            entry_price = ticker["bid"] # hit the bif to go short

        return {
            "symbol": self.symbol,
            "side": "BUY" if bias == "long" else "SELL",
            "price": entry_price,
            "tp": tp,
            "sl": sl,
            "timestamp": now_utc(),
        }

    def get_ticker(self, symbol: str):
        """
        Return a dict with at least {'bid': float, 'ask': float}.
        """
        return self.client.fetch_ticker(symbol)
