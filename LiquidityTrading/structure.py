# structure.py
import pandas as pd
import numpy as np
import logging
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

def ema_trend_signal(df: pd.DataFrame,
                     length: int = 150,
                     backcandles: int = 15) -> pd.Series:
    """
    Compute the “all‐above / all‐below” EMA signal on whatever timeframe
    DataFrame you pass in (must have open/high/low/close).
    Returns a pd.Series of 0/1/2/3 indexed the same as df.
      0 = no clear trend (or insufficient data for this bar),
      1 = downtrend, 2 = uptrend, 3 = straddle/ranging.
    """
    df = df.copy()
    df['EMA'] = ta.ema(df['close'], length=length)

    sig = np.zeros(len(df), dtype=int)

    # start at backcandles (we need at least that many bars)
    for i in range(backcandles, len(df)):
        idx = df.index[i]
        window = df.iloc[i-backcandles:i+1]

        # if any EMA is missing, skip this bar
        if window['EMA'].isna().any():
            logging.debug(f"EMA signal: insufficient EMA data at {idx}, skipping")
            continue

        # test “all closes & opens above EMA” & “all below”
        upt = all(min(row[['open', 'close']]) > row['EMA']
                  for _, row in window.iterrows())
        dnt = all(max(row[['open', 'close']]) < row['EMA']
                  for _, row in window.iterrows())

        if upt and dnt:
            sig[i] = 3
        elif upt:
            sig[i] = 2
        elif dnt:
            sig[i] = 1
        # else remains 0

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
