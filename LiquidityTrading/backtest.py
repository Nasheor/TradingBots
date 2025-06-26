#!/usr/bin/env python3
import time
import pandas as pd
from decimal import Decimal
from exchange import EX
from sessions import get_session, detect_sweep
from structure import ema_trend_signal, detect_structure
from strategy import build_trade
from config import  RISK_PER_TRADE, LEVERAGE

# ────────────────────────────────────────────────────────────────────────────
# Helper to page through Binance’s 1000-bar limit
# ────────────────────────────────────────────────────────────────────────────
def fetch_full(symbol, timeframe):
    since = EX.parse8601('2025-01-01T00:00:00Z')
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
# Backtest one symbol’s performance using the live‐bot logic
# ────────────────────────────────────────────────────────────────────────────
def backtest_symbol(symbol):
    print(f"\n▶️ Backtesting {symbol}")
    # 1) load data
    df5   = fetch_full(symbol, '5m')
    df2h  = fetch_full(symbol, '2h')
    if df5.empty or df2h.empty:
        print("   insufficient data, skipping")
        return pd.DataFrame()

    # 2) compute 2h EMA-trend signal
    sig2h = ema_trend_signal(df2h, length=150, backcandles=15)

    balance    = 1_000.0
    trades     = []
    cnt_sweep  = cnt_trend = cnt_struct = 0

    # 3) iterate by calendar day
    for date, day5 in df5.groupby(df5.index.date):
        # slice sessions
        asia     = day5[day5.index.map(lambda t: get_session(t)=='Asia')]
        london   = day5[day5.index.map(lambda t: get_session(t)=='London')]
        killzone = day5[day5.index.map(lambda t: get_session(t)=='KillZone')]
        if asia.empty or london.empty or killzone.empty:
            continue

        # a) sweep bias
        bias = detect_sweep(asia, london)
        if not bias:
            continue
        cnt_sweep += 1

        # b) trend-alignment at first KillZone candle
        first_kz = killzone.index[0]
        sig_sub  = sig2h[:first_kz]
        if sig_sub.empty:
            continue
        last_sig = sig_sub.iat[-1]
        if bias=='low' and last_sig!=2:
            continue
        if bias=='high' and last_sig!=1:
            continue
        cnt_trend += 1

        # c) preliminary TP/SL from first kill-zone candle
        tr = build_trade(killzone, bias, balance, asia, london)
        if not tr:
            continue

        # # d) detect first 5m structure shift (full-day context)
        # day_full   = day5.reset_index(drop=False)  # integer positions 0..N-1
        # timestamps = day_full['ts']
        # entry_price = None
        # for ts in killzone.index:
        #     pos = timestamps.searchsorted(ts)
        #     if detect_structure(day_full, pos, backcandles=30, pivot_window=5):
        #         print("Change of structure detected")
        #         entry_price = float(day_full.iloc[pos]['close'])
        #         cnt_struct += 1
        #         break
        # d) ENTRY: first 200-EMA crossover inside KillZone
        kz = killzone.copy()
        kz['EMA200'] = kz['close'].ewm(span=200, adjust=False).mean()
        entry_price = None

        for ts, row in kz.iterrows():
            price = float(row['close'])
            ema = float(row['EMA200'])
            if bias == 'low' and price >= ema:
                entry_price = price
                cnt_struct += 1
                break
            if bias == 'high' and price <= ema:
                entry_price = price
                cnt_struct += 1
                break

        # fallback → first kill-zone close if no shift
        if entry_price is None:
            entry_price = float(killzone.iloc[0]['close'])

        # e) final sizing: 2% risk & leverage
        sl_diff  = abs(entry_price - tr['sl'])
        risk_usd = balance * RISK_PER_TRADE
        qty_risk = risk_usd / sl_diff
        qty_margin = risk_usd * LEVERAGE / entry_price
        raw_qty = min(qty_risk, qty_margin)

        # f) simulate trade exit
        rem = killzone[killzone.index >= killzone.index[0]]
        exit_price = None
        result = None
        for _, row in rem.iterrows():
            h, l = row['high'], row['low']
            if bias=='low':
                if l <= tr['sl']:
                    exit_price, result = tr['sl'], 'SL'; break
                if h >= tr['tp']:
                    exit_price, result = tr['tp'], 'TP'; break
            else:
                if h >= tr['sl']:
                    exit_price, result = tr['sl'], 'SL'; break
                if l <= tr['tp']:
                    exit_price, result = tr['tp'], 'TP'; break
        if exit_price is None:
            exit_price, result = rem.iloc[-1]['close'], 'END'

        pnl      = ((exit_price - entry_price) if bias=='low'
                    else (entry_price - exit_price)) * raw_qty
        balance += pnl

        trades.append({
            'date':    date,
            'symbol':  symbol,
            'bias':    bias,
            'entry':   entry_price,
            'tp':      tr['tp'],
            'sl':      tr['sl'],
            'exit':    exit_price,
            'result':  result,
            'pnl':     pnl,
            'balance': balance
        })

    # 4) summary
    print(f" sweeps={cnt_sweep}, trend_ok={cnt_trend}, struct_hits={cnt_struct}, trades={len(trades)}")
    return pd.DataFrame(trades)

# ────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    universe = ['BTC/USDT', 'ETH/USDT']
    # universe = ['SOL/USDT']
    all_trades = []
    for sym in universe:
        df_tr = backtest_symbol(sym)
        if not df_tr.empty:
            file = sym.split('/')[0]+"_backtest.csv"
            df_tr.to_csv(file, index=False)
        else:
            print("\n⚠️ No trades generated for "+sym+"; check your filters!")
