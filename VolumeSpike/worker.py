import ccxt
import pandas as pd
import requests
import time
import os
import numpy as np
from openai import OpenAI

# === CONFIG ===
BINANCE = ccxt.binance()
WEBHOOK_URL = os.getenv("DISCORD_VOLUME_URL")
OPENAI_API_KEY = os.getenv("OPEN_API_KEY")
TIMEFRAME = '15m'
WINDOW = 50
SPIKE_FACTOR = 2.0
MAX_COINS = 50
OBV_WINDOW = 50

client = OpenAI(api_key=OPENAI_API_KEY)

# def get_top_volatile_coins(limit=MAX_COINS):
#     markets = BINANCE.load_markets()
#     usdt_markets = [symbol for symbol in markets if symbol.endswith('/USDT') and ':' not in symbol]
#     return usdt_markets[:limit]


def get_top_volatile_coins(limit=MAX_COINS, lookback=20):
    try:
        markets = BINANCE.fetch_markets()
        usdt_perp_markets = [
            m for m in markets
            if m.get('linear') and m.get('contract') and m['quote'] == 'USDT'
               and m['active'] and m.get('swap')
        ]

        vol_data = []
        for market in usdt_perp_markets:
            try:
                funding_data = BINANCE.fapiPublicGetFundingRate({'symbol': market['id'], 'limit': lookback})
                if not funding_data:
                    continue

                df = pd.DataFrame(funding_data)
                df['fundingRate'] = df['fundingRate'].astype(float)

                volatility = df['fundingRate'].std()
                latest_funding = df['fundingRate'].iloc[-1]

                vol_data.append((market['symbol'].replace(':USDT', ''), volatility, latest_funding))
            except Exception:
                continue

        ranked = sorted(vol_data, key=lambda x: x[1], reverse=True)
        top_symbols = [symbol for symbol, _, _ in ranked[:limit]]
        return top_symbols

    except Exception as e:
        print(f"[ERROR] Failed to fetch funding volatility: {e}")
        return []


def fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100):
    try:
        ohlcv = BINANCE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error fetching OHLCV for {symbol}: {e}")
        return None


def is_volume_spike(df, window=WINDOW, spike_factor=SPIKE_FACTOR):
    if len(df) < window + 1:
        return False, 0, 0
    recent_volume = df['volume'].iloc[-1]
    avg_volume = df['volume'].iloc[-window-1:-1].mean()
    return recent_volume > spike_factor * avg_volume, recent_volume, avg_volume


def calculate_obv(df):
    df['direction'] = np.where(df['close'] > df['close'].shift(1), 1,
                        np.where(df['close'] < df['close'].shift(1), -1, 0))
    df['obv'] = (df['volume'] * df['direction']).cumsum()
    return df


def obv_divergence(df, window=OBV_WINDOW):
    if 'obv' not in df.columns:
        df = calculate_obv(df)
    if len(df) < window:
        return None
    price_slope = np.polyfit(range(window), df['close'].iloc[-window:], 1)[0]
    obv_slope = np.polyfit(range(window), df['obv'].iloc[-window:], 1)[0]

    if price_slope > 0 and obv_slope < 0:
        return "🔻 Bearish OBV divergence"
    elif price_slope < 0 and obv_slope > 0:
        return "🔺 Bullish OBV divergence"
    else:
        return None


def confluence_score(volume_spike, obv_signal):
    score = 0
    if volume_spike:
        score += 1
    if obv_signal:
        score += 1
    return score


def gpt_reasoning(symbol, price, volume, avg_volume, obv_sig):
    prompt = f"""
    You are an expert crypto trading assistant. Analyze the following alert and provide 2-3 sentence insight and 
    direction of trade to place:
    
    - Symbol: {symbol}
    - Price: ${price:.2f}
    - Volume: {volume:.2f} vs Avg: {avg_volume:.2f} ({volume/avg_volume:.2f}x)
    - OBV Signal: {obv_sig}
    - Timeframe: {TIMEFRAME}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "You analyse crypto trade setups with detailed reasoning."},
                      {"role": "user", "content": prompt}
                      ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] GPT failed: {e}")
        return "❌ GPT analysis unavailable."


def send_discord_alert(symbol, price, volume, avg_volume, obv_sig, gpt_msg, score):
    message = (
        f"📊 **Volume Spike + OBV Alert  {symbol}**\n"
        f"💰 Price: ${price:.2f}\n"
        f"🔺 Volume: {volume:.2f} vs Avg: {avg_volume:.2f} ({volume / avg_volume:.2f}x)\n"
        f"{obv_sig}\n"
        f"🧠 GPT: {gpt_msg}\n"
        f"🔹 Confluence Score: {score}\n"
        f"🕒 Timeframe: {TIMEFRAME}"
    )
    if len(message) > 2000:
        message = message[:1999]
    response = requests.post(WEBHOOK_URL, json={"content": message})
    if response.status_code != 204:
        print(f"[ERROR] Failed to send Discord message: {response.text}")


def main():
    coins = get_top_volatile_coins()
    for symbol in coins:
        df = fetch_ohlcv(symbol)
        if df is None or df.empty:
            continue

        volume_spike, volume, avg_volume = is_volume_spike(df)
        obv_sig = obv_divergence(df)
        score = confluence_score(volume_spike, obv_sig)

        if score > 1:
            price = df['close'].iloc[-1]
            gpt_msg = gpt_reasoning(symbol, price, volume, avg_volume, obv_sig)
            # print(gpt_msg)
            send_discord_alert(symbol, price, volume, avg_volume, obv_sig, gpt_msg, score)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
