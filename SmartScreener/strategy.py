# ─── File: strategy.py ───

import pandas as pd
from exchange import EXCHANGE

def compute_indicators(symbol):
    ohlcv = None
    try:
        # print(f"[DEBUG] Fetching OHLCV for {symbol}")
        ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe='5m', limit=300)
        df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        df['EMA_200'] = df['close'].ewm(span=200).mean()
        df['RSI'] = compute_rsi(df['close'], 80)


        trend_2h = EXCHANGE.fetch_ohlcv(symbol, timeframe='1h', limit=300)
        df_2h = pd.DataFrame(trend_2h, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        slope = df_2h['close'].ewm(span=200).mean().diff().iloc[-1]

        return {
            'trend_slope': slope,
            '2m_close': df['close'].iloc[-1],
            '2m_ema200': df['EMA_200'].iloc[-1],
            'RSI': df['RSI'].iloc[-1]
        }
    except Exception:
        print(f"[DEBUG] No OHLCV returned for {symbol}")
        if not ohlcv or len(ohlcv) < 200:
            print(f"[DEBUG] Insufficient OHLCV for {symbol}")
            print(ohlcv)
        return None

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))