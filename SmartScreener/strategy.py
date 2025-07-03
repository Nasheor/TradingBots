# ─── File: strategy.py ───

import pandas as pd
from exchange import EXCHANGE


def compute_indicators(symbol):
    try:
        ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe='3m', limit=300)
        df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])

        df['EMA_200'] = df['close'].ewm(span=200).mean()
        df['RSI'] = compute_rsi(df['close'], 14)
        slope = df['EMA_200'].diff().iloc[-1]

        price = df['close'].iloc[-1]
        ema200 = df['EMA_200'].iloc[-1]
        rsi = df['RSI'].iloc[-1]
        current_vol = df['vol'].iloc[-1]
        avg_vol = df['vol'].rolling(50).mean().iloc[-1]  # 50 period average volume

        confluence = []
        if slope > 0:
            confluence.append("Uptrend (EMA slope positive)")
        elif slope < 0:
            confluence.append("Downtrend (EMA slope negative)")

        if price > ema200:
            confluence.append("Price above EMA200 (bullish)")
        elif price < ema200:
            confluence.append("Price below EMA200 (bearish)")

        if rsi < 30:
            confluence.append("RSI oversold (<30) potential long")
        elif rsi > 70:
            confluence.append("RSI overbought (>70) potential short")

        if current_vol > avg_vol * 1.5:
            confluence.append(f"Volume spike ({current_vol:.0f} vs {avg_vol:.0f})")
        elif current_vol < avg_vol * 0.5:
            confluence.append(f"Volume unusually low ({current_vol:.0f} vs {avg_vol:.0f})")
            # Sweep detection (basic logic)
            recent_low = df['low'].rolling(5).min().iloc[-2]
            recent_high = df['high'].rolling(5).max().iloc[-2]

            if df['low'].iloc[-1] < recent_low:
                confluence.append("Recent liquidity sweep low")
            if df['high'].iloc[-1] > recent_high:
                confluence.append("Recent liquidity sweep high")
        # Sweep detection (basic logic)
        recent_low = df['low'].rolling(5).min().iloc[-2]
        recent_high = df['high'].rolling(5).max().iloc[-2]

        if df['low'].iloc[-1] < recent_low:
            confluence.append("Recent liquidity sweep low")
        if df['high'].iloc[-1] > recent_high:
            confluence.append("Recent liquidity sweep high")
        return {
            'slope': slope,
            'price': price,
            'ema200': ema200,
            'rsi': rsi,
            'volume': current_vol,
            'avg_volume': avg_vol,
            'confluence': ", ".join(confluence)
        }

    except Exception as e:
        print(f"[ERROR] Failed to compute indicators for {symbol}: {e}")
        return None

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))