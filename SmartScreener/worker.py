# Project: VolatilityVision - GPT-Powered Smart Screener for Futures

# ─── File: worker.py ───
import pandas as pd
from config import LOOKBACK_PERIOD, TOP_N
from exchange import EXCHANGE
from strategy import compute_indicators, compute_rsi
from analyse_result import gpt_rank_setups
from discord_notify import send_discord_message

# -------- Main Screener ---------
def fetch_top_funding_volatile_symbols():
    markets = EXCHANGE.fetch_markets()

    usdt_perp_markets = [
        m for m in markets
        if m.get('linear') and m.get('contract') and m['quote'] == 'USDT'
           and m['active'] and m.get('swap')
    ]

    print(f"[DEBUG] Found {len(usdt_perp_markets)} USDT-M perpetual markets")
    # print(f"[DEBUG] Filtered USDT-M Perpetuals: {[m['symbol'] for m in usdt_perp_markets]}")

    vol_data = []
    for market in usdt_perp_markets:
        try:
            funding = EXCHANGE.fapiPublicGetFundingRate({'symbol': market['id'], 'limit': LOOKBACK_PERIOD})
            if not funding:
                continue
            df = pd.DataFrame(funding)
            df['fundingRate'] = df['fundingRate'].astype(float)
            latest_funding = df['fundingRate'].iloc[-1]
            volatility = df['fundingRate'].std()
            vol_data.append((market['symbol'].split(":")[0], volatility))
        except Exception as e:
            continue

    ranked = sorted(vol_data, key=lambda x: x[1], reverse=True)
    return [s[0] for s in ranked[:TOP_N]]

def run_screener():
    top_symbols = fetch_top_funding_volatile_symbols()
    print(f"[INFO] Top {TOP_N} volatile USDT-M symbols: {top_symbols}")

    setups = []
    for sym in top_symbols:
        indicators = compute_indicators(sym)
        if indicators:
            setups.append({'symbol': sym, **indicators})

    if not setups:
        print("[INFO] No valid setups found.")
        return

    ranked_output = gpt_rank_setups(setups)
    print("\n[GPT Rankings]\n", ranked_output)

    discord_intro = "🚀 **VolatilityVision - GPT Ranked Setups** 🚀\n"
    discord_msg = discord_intro + ranked_output
    send_discord_message(discord_msg)

    # TODO: Store to DynamoDB or S3 as per dynamo.py logic

def format_discord_message(setups):
    msg = "🚀 **VolatilityVision - Top Trade Setups** 🚀\n"
    for s in setups:
        msg += (
            f"\n🔹 {s['symbol']}\n"
            f"• Trend: {'Uptrend' if s['slope'] > 0 else 'Downtrend'} (EMA slope {s['slope']:.5f})\n"
            f"• Price: {s['price']:.2f} {'above' if s['price'] > s['ema200'] else 'below'} EMA200\n"
            f"• RSI: {s['rsi']:.2f} {'(Oversold, potential long)' if s['rsi'] < 30 else '(Overbought, potential short)' if s['rsi'] > 70 else ''}\n"
            f"• Volume: {s['volume']:.0f} ({'Spike' if s['volume'] > s['avg_volume'] * 1.5 else 'Low' if s['volume'] < s['avg_volume'] * 0.5 else 'Normal'})\n"
            f"• Confluence: {s['confluence']}\n"
            f"-------------------------\n"
        )
    return msg

# -------- Run ---------
if __name__ == "__main__":
    run_screener()

# ─── Supporting File Breakdown ───

# config.py: API keys, constants, model config
# exchange.py: Binance connection setup with ccxt
# strategy.py: compute_indicators and compute_rsi logic
# analyse_result.py: gpt_rank_setups with GPT prompt and response handling
# dynamo.py: Functions to save results to DynamoDB/S3 (extend as needed)
# orders.py, structure.py, sessions.py: Reusable components from existing trading bot architecture

# Repo: LiquidityTrading upgraded to VolatilityVision
# Purpose: Screen top 50 volatile funding coins for clean setups with GPT insights
# Next Step: Hook result storage & optional alerts


# Tasks Remaining
# 1. Test it with back testing
