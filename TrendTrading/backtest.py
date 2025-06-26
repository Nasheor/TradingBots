#!/usr/bin/env python3
"""
backtest.py

Backtest the TrendStrategy across one or more symbols, using full history from a start date.
Generates per-trade statistics and writes a CSV per symbol.
"""
import time
import logging
import pandas as pd
from decimal import Decimal
from exchange import EX
from strategy import TrendStrategy
from config import RISK_PER_TRADE, LEVERAGE, TIMEFRAME_TREND, TIMEFRAME_TRADE

# ────────────────────────────────────────────────────────────────────────────
# Helper to page through Binance’s 1000-bar limit
# ────────────────────────────────────────────────────────────────────────────
def fetch_full(symbol: str, timeframe: str, since_iso: str):
    since = EX.parse8601(since_iso)
    all_bars = []
    while True:
        batch = EX.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        all_bars += batch
        since = batch[-1][0] + 1
        time.sleep(EX.rateLimit / 1000)
    df = pd.DataFrame(all_bars, columns=['ts','open','high','low','close','volume'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df.set_index('ts', inplace=True)
    return df

# ────────────────────────────────────────────────────────────────────────────
# Backtest one symbol’s performance using the TrendStrategy logic
# ────────────────────────────────────────────────────────────────────────────
def backtest_symbol(symbol: str, since_iso: str = '2024-01-01T00:00:00Z') -> pd.DataFrame:
    logging.info(f"> Backtesting {symbol}")

    # 1) load data
    df_trade = fetch_full(symbol, TIMEFRAME_TRADE, since_iso)
    df_trend = fetch_full(symbol, TIMEFRAME_TREND, since_iso)
    if df_trade.empty or df_trend.empty:
        logging.warning(f"Insufficient data for {symbol}, skipping")
        return pd.DataFrame()

    # 2) setup client & strategy
    class BacktestClient:
        def __init__(self, trend_df, trade_df):
            self.trend_df = trend_df
            self.trade_df = trade_df
            self.idx_map = {TIMEFRAME_TREND: 0, TIMEFRAME_TRADE: 0}

        def get_klines(self, symbol, interval, limit):
            df = self.trend_df if interval == TIMEFRAME_TREND else self.trade_df
            end_idx = self.idx_map[interval]
            data = df.iloc[:end_idx]
            return [
                {"open": row['open'], "high": row['high'], "low": row['low'], "close": row['close']}
                for _, row in data.iterrows()
            ]

        def get_ticker(self, symbol):
            price = self.trade_df.iloc[self.idx_map[TIMEFRAME_TRADE]-1]['close']
            return {'bid': price, 'ask': price}

    client = BacktestClient(df_trend, df_trade)
    strategy = TrendStrategy(client, symbol, TIMEFRAME_TREND, TIMEFRAME_TRADE)

    # 3) backtest loop
    balance = 1_000.0
    trades = []
    for i in range(len(df_trade)):
        client.idx_map[TIMEFRAME_TRADE] = i + 1
        ts = df_trade.index[i]
        idx_tr = df_trend.index.searchsorted(ts, side='right')
        client.idx_map[TIMEFRAME_TREND] = int(idx_tr)

        # skip warm-up
        if client.idx_map[TIMEFRAME_TRADE] < strategy.ema_period + 5 or \
           client.idx_map[TIMEFRAME_TREND] < strategy.ema_period + strategy.lookback:
            continue

        entry = strategy.on_new_trade_candle()
        if not entry:
            continue

        entry_price = entry['price']
        tp = entry['tp']
        sl = entry['sl']
        side = entry['side']

        # guard against zero SL distance
        sl_diff = abs(entry_price - sl)
        if sl_diff == 0:
            logging.warning(f"{symbol} {ts}: zero SL distance (entry==sl); skipping trade")
            continue

        # simulate exit
        exit_price, result = None, None
        for j in range(i+1, len(df_trade)):
            high = df_trade.iloc[j]['high']
            low = df_trade.iloc[j]['low']
            if side == 'BUY':
                if low <= sl:
                    exit_price, result = sl, 'SL'
                    break
                if high >= tp:
                    exit_price, result = tp, 'TP'
                    break
            else:
                if high >= sl:
                    exit_price, result = sl, 'SL'
                    break
                if low <= tp:
                    exit_price, result = tp, 'TP'
                    break
        if exit_price is None:
            exit_price = df_trade.iloc[-1]['close']
            result = 'END'

        # size trade
        qty = (balance * RISK_PER_TRADE * LEVERAGE) / sl_diff
        pnl = ((exit_price - entry_price) if side=='BUY' else (entry_price - exit_price)) * qty
        balance += pnl

        trades.append({
            'ts': ts,
            'symbol': symbol,
            'side': side,
            'entry': entry_price,
            'tp': tp,
            'sl': sl,
            'exit': exit_price,
            'result': result,
            'pnl': pnl,
            'balance': balance
        })

    df_trades = pd.DataFrame(trades)
    if not df_trades.empty:
        file = symbol.replace('/', '') + '_backtest.csv'
        df_trades.to_csv(file, index=False)
        logging.info(f"{symbol}: trades={len(df_trades)}, final balance={balance:.2f}")
    else:
        logging.info(f"{symbol}: no trades executed")
    return df_trades

# ────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    universe = ['BTC/USDT', 'ETH/USDT']
    for sym in universe:
        backtest_symbol(sym)
