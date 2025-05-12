# structure.py
import pandas as pd
import numpy as np
from decimal import Decimal
from scipy import stats  # if you need it later
import pandas_ta as ta

def load_htf_df(symbol, ex, lookback=500):
    """
    Fetch the last `lookback` 1h candles from ccxt exchange `ex`
    and return a DataFrame with typical columns.
    """
    ohlcv = ex.fetch_ohlcv(symbol, '1h', limit=lookback)
    df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','vol'])
    df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
    df.set_index('ts', inplace=True)
    return df

def ema_trend_signal(df, length=150, backcandles=15):
    """
    Replicates your EMAsignal logic: returns a Series of 0/1/2/3.
    """
    df = df.copy()
    df['EMA'] = ta.ema(df['close'], length=length)
    sig = np.zeros(len(df), dtype=int)
    for i in range(backcandles, len(df)):
        upt = all(min(df.iloc[j][['open','close']]) > df.iloc[j]['EMA']
                  for j in range(i-backcandles, i+1))
        dnt = all(max(df.iloc[j][['open','close']]) < df.iloc[j]['EMA']
                  for j in range(i-backcandles, i+1))
        if upt and dnt:
            sig[i] = 3
        elif upt:
            sig[i] = 2
        elif dnt:
            sig[i] = 1
    return pd.Series(sig, index=df.index, name='EMASignal')

def is_pivot(df, idx, window=5):
    """Return 1=high pivot, 2=low pivot, 3=both, 0=none."""
    if idx - window < 0 or idx + window >= len(df):
        return 0
    low  = df.iloc[idx]['low']
    high = df.iloc[idx]['high']
    block = df.iloc[idx-window:idx+window+1]
    high_pivot = int(all(high >= block['high']))
    low_pivot  = int(all(low  <= block['low']))
    return high_pivot + 2*low_pivot

def detect_structure(df, candle_idx, backcandles=30, pivot_window=5):
    """
    Returns True if a valid change‐of‐structure is detected at `candle_idx`
    in the direction implied by the last three pivots.
    """
    # mark pivots
    pivots = [is_pivot(df, i, pivot_window) for i in range(len(df))]
    df = df.assign(isPivot=pivots)

    # slice the lookback area
    start = max(0, candle_idx - backcandles - pivot_window)
    end   = candle_idx - pivot_window
    local = df.iloc[start:end]

    highs = local[local.isPivot==1]
    lows  = local[local.isPivot==2]
    # we need at least 3 of each
    if len(highs) < 3 or len(lows) < 3:
        return False

    # take the last 3 indices & values
    h_idx = highs.index[-3:]
    l_idx = lows.index[-3:]
    h_val = highs['high'].values[-3:]
    l_val = lows['low'].values[-3:]

    # your pattern tests (order + diff + structure)
    ord_ok = (l_idx[0] < h_idx[0] < l_idx[1] < h_idx[1] < l_idx[2] < h_idx[2])
    lim1, lim2 = 0.005, 0.005/3
    dif_ok = (abs(l_val[0]-h_val[0])>lim1 and
              abs(h_val[0]-l_val[1])>lim2 and
              abs(h_val[1]-l_val[1])>lim1 and
              abs(h_val[1]-l_val[2])>lim2)
    pat1 = (l_val[0] < h_val[0] and
            l_val[1] > l_val[0] and l_val[1] < h_val[0] and
            h_val[1] > h_val[0] and
            l_val[2] > l_val[1] and l_val[2] < h_val[1] and
            h_val[2] < h_val[1] and h_val[2] > l_val[2])
    pat2 = (l_val[0] < h_val[0] and
            l_val[1] > l_val[0] and l_val[1] < h_val[0] and
            h_val[1] > h_val[0] and
            l_val[2] < l_val[1] and
            h_val[2] < h_val[1])

    return ord_ok and dif_ok and (pat1 or pat2)
