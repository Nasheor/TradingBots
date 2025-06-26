#!/usr/bin/env python3
import pandas as pd
import plotly.graph_objects as go
from structure import is_pivot, detect_structure
from exchange import EX  # your ccxt singleton

# ─────────────────────────────────────────────────────────────────────────────
symbol    = 'SOL/USDT'
timeframe = '5m'
limit     = 500

# 1) Fetch the last 500 5-minute bars
ohlcv = EX.fetch_ohlcv(symbol, timeframe, limit=limit)
df    = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','vol'])
df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
df.set_index('ts', inplace=True)

# 2) Compute pivots (swing points)
pivot_window = 5
df['pivot'] = [is_pivot(df, i, pivot_window) for i in range(len(df))]

# 3) Compute BOS/CHOCH flags
#    detect_structure needs integer positions, so hand it a small reset-index df each time:
reset = df.reset_index(drop=False)
bos_flags = [detect_structure(reset, idx, backcandles=30, pivot_window=pivot_window)
             for idx in range(len(reset))]
df['bos'] = bos_flags

print(f"Found {df['bos'].sum()} structure-break flags in the last {limit} bars")

# 4) Build the chart
fig = go.Figure()

# 4a) Candlesticks
fig.add_trace(go.Candlestick(
    x=df.index, open=df.open, high=df.high, low=df.low, close=df.close,
    name='5m candles', showlegend=False
))

# 4b) Pivot-highs (1 or 3)
highs = df[df['pivot'].isin([1,3])]
fig.add_trace(go.Scatter(
    x=highs.index, y=highs.high + 0.1,
    mode='markers',
    marker=dict(color='red', symbol='triangle-up', size=8),
    name='Pivot High'
))

# 4c) Pivot-lows (2 or 3)
lows = df[df['pivot'].isin([2,3])]
fig.add_trace(go.Scatter(
    x=lows.index, y=lows.low - 0.1,
    mode='markers',
    marker=dict(color='green', symbol='triangle-down', size=8),
    name='Pivot Low'
))

# 4d) BOS / CHoCH diamonds at the close price
bos_pts = df[df['bos']]
fig.add_trace(go.Scatter(
    x=bos_pts.index,
    y=bos_pts['close'],
    mode='markers',
    marker=dict(color='orange', symbol='diamond', size=12),
    name='BOS/CHOCH'
))

# 5) Final layout tweaks
fig.update_layout(
    title=f"{symbol} 5m – swings & structure breaks",
    xaxis_title='Time (UTC)',
    yaxis_title='Price',
    template='plotly_dark',
    xaxis_rangeslider_visible=False
)

fig.show()
