import time
import ccxt
import pandas as pd
from decimal import Decimal
from sessions    import get_session, detect_sweep
from structure   import ema_trend_signal, detect_structure
from strategy    import build_trade
from exchange    import EX, fetch_price          # EX is your ccxt.binanceusdm instance
from config      import RISK_PER_TRADE, LEVERAGE, RR_STATIC

# ─────────────────────────────────────────────────────────────────────────────
# helper to fetch _all_ 5m bars since a given date
# ─────────────────────────────────────────────────────────────────────────────
def fetch_full(symbol, timeframe='5m', since_iso='2025-01-01T00:00:00Z'):
    since = EX.parse8601(since_iso)
    all_bars = []
    while True:
        batch = EX.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        all_bars += batch
        # next fetch starts 1ms after the last bar we got
        since = batch[-1][0] + 1
        time.sleep(EX.rateLimit / 1000)
    df = pd.DataFrame(all_bars, columns=['ts','open','high','low','close','vol'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df.set_index('ts', inplace=True)
    return df

# ─────────────────────────────────────────────────────────────────────────────
# backtest one symbol
# ─────────────────────────────────────────────────────────────────────────────
def backtest_symbol(symbol):
    df5 = fetch_full(symbol, '5m')
    # fetch a large chunk of 2h history up front
    df2h = pd.DataFrame(
        EX.fetch_ohlcv(symbol, '2h', limit=5000),
        columns=['ts','open','high','low','close','vol']
    )
    df2h['ts'] = pd.to_datetime(df2h['ts'], unit='ms', utc=True)
    df2h.set_index('ts', inplace=True)
    sig2h = ema_trend_signal(df2h, length=50, backcandles=8)

    balance = 1_000.0
    trades  = []

    # group by calendar date
    for date, day5 in df5.groupby(df5.index.date):
        # session slices
        asia   = day5[day5.index.map(lambda t: get_session(t)=='Asia')]
        london = day5[day5.index.map(lambda t: get_session(t)=='London')]
        kill   = day5[day5.index.map(lambda t: get_session(t)=='KillZone')]
        if asia.empty or london.empty or kill.empty:
            continue

        # 1) sweep bias
        bias = detect_sweep(asia, london)
        if not bias:
            continue

        # 2) trend alignment (2h)
        # pick the last signal up to start of killzone (14:00)
        cut = pd.Timestamp(f"{date}T14:00:00Z")
        try:
            last_sig = sig2h[:cut].iat[-1]
        except IndexError:
            continue
        if bias=='low'  and last_sig != 2: continue
        if bias=='high' and last_sig != 1: continue

        # 3) preliminary SL/TP
        tr = build_trade(kill, bias, balance, asia, london)
        if not tr:
            continue

        # 4) wait for structure shift
        kz = kill.reset_index(drop=False)
        entry_price = None
        for idx in range(len(kz)):
            struct = detect_structure(kz, idx, backcandles=30, pivot_window=5)
            if bias=='low'  and struct=='bullish':
                entry_price = float(kz.loc[idx,'close'])
                break
            if bias=='high' and struct=='bearish':
                entry_price = float(kz.loc[idx,'close'])
                break
        if entry_price is None:
            continue

        # 5) size position: risk exactly 2%
        sl_diff    = abs(entry_price - tr['sl'])
        risk_usd   = balance * RISK_PER_TRADE
        qty_risk   = risk_usd / sl_diff
        qty_margin = risk_usd * LEVERAGE / entry_price
        qty        = min(qty_risk, qty_margin)

        # 6) simulate TP/SL
        rem = kill[kill.index >= kz.loc[idx,'ts']]
        exit_price, result = None, None
        for _, row in rem.iterrows():
            if bias=='low':
                if row['low'] <= tr['sl']:
                    exit_price, result = tr['sl'], 'SL'
                    break
                if row['high']>= tr['tp']:
                    exit_price, result = tr['tp'], 'TP'
                    break
            else:
                if row['high']>= tr['sl']:
                    exit_price, result = tr['sl'], 'SL'
                    break
                if row['low'] <= tr['tp']:
                    exit_price, result = tr['tp'], 'TP'
                    break
        if exit_price is None:
            exit_price, result = rem.iloc[-1]['close'], 'END'

        pnl = ((exit_price-entry_price) if bias=='low'
               else (entry_price-exit_price)) * qty
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

    return pd.DataFrame(trades)


if __name__ == '__main__':
    symbols = ['SOL/USDT','XRP/USDT','LINK/USDT','LTC/USDT']
    all_df  = [backtest_symbol(s) for s in symbols]
    out     = pd.concat(all_df, ignore_index=True)
    out.to_csv('killzone_backtest.csv', index=False)
    print("✅ Backtest complete — results in killzone_backtest.csv")
