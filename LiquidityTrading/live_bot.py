#!/usr/bin/env python3
"""
live_bot.py – Liquidity‑sweep Kill‑Zone strategy for Binance USDT‑M Futures
Update 2025‑04‑23: limit **one trade per symbol per UTC‑day**
--------------------------------------------------------------------------------
• Adds `trade_taken` flag that resets at midnight UTC.
• If a trade has already been entered for the day (regardless of outcome)
  the thread ignores further setups until the next day.
• Keeps all previous margin, sizing and logging improvements.
"""
import os, time, datetime as dt
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import ccxt
import pandas as pd
import socket
import logging

# ───────────────────────── LOGGING ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("/home/ec2-user/TradingBots/LiquidityTrading/live_bot.log"),
        logging.StreamHandler()
    ]
)

# ───────────────────────── CONFIG ───────────────────────────
RISK_PER_TRADE = 0.02     # 2 % of free balance
LEVERAGE       = 25
RR_STATIC      = 3.0
TIMEFRAME      = '5m'
SYMBOLS        = ['SOL/USDT','XRP/USDT','LINK/USDT','BTC/USDT','ETH/USDT','LTC/USDT']
TESTNET        = False
API_KEY        = "rdpvKsuXdhdNXHPAM7XgZ6sfCQXLXBvfNFLMEQZNqaeHilqbIREar8LXWj65x8z8"
API_SECRET     = "jciGO3TOYa5CSHVS1qWG2H0gV7hCtiRyC8eM3x5x3AqiRN2iXg91Z3uapXDsieLx"

logging.info(f"Host {socket.gethostname()}")

# ─────────────────────── Exchange init ──────────────────────

def make_exchange():
    base='https://testnet.binancefuture.com' if TESTNET else 'https://fapi.binance.com'
    ex=ccxt.binanceusdm({
        'apiKey':API_KEY,
        'secret':API_SECRET,
        'enableRateLimit':True,
        'options':{'defaultType':'future'},
        'urls':{'api':{'public':base,'private':base}}
    })
    ex.load_markets()
    return ex

ex = make_exchange()

# ───────────────────────── HELPERS ──────────────────────────

def get_session(ts):
    h = ts.hour
    return 'Asia' if h < 5 else 'London' if h < 11 else 'KillZone' if 14 <= h < 17 else 'Off'

def market_info(sym):
    m = ex.market(sym)
    p_prec = m['precision']['price'] or 2
    q_prec = m['precision']['amount'] or 3
    min_qty = m['limits']['amount']['min'] or 0.0
    min_cost = m['limits']['cost']['min'] or 0.0
    return int(p_prec), int(q_prec), float(min_qty), float(min_cost)

def d_round(val, prec):
    try:
        return float(Decimal(val).quantize(Decimal(f'1e-{prec}'), ROUND_DOWN))
    except InvalidOperation:
        return val

def detect_sweep(a, l):
    if a.empty or l.empty:
        return None
    a_hi, a_lo = a['high'].max(), a['low'].min()
    l_hi, l_lo = l['high'].max(), l['low'].min()
    if l_hi > a_hi and l_lo < a_lo:
        return 'both'
    if l_hi > a_hi:
        return 'high'
    if l_lo < a_lo:
        return 'low'
    return None

def build_trade(k_df, sweep, avail):
    if k_df.empty or sweep in (None, 'both'):
        return None
    c0 = k_df.iloc[0]
    entry = c0['close']
    direction = 'short' if sweep == 'high' else 'long'
    sl = c0['low'] * 0.999 if direction == 'long' else c0['high'] * 1.001
    dist = abs(entry - sl)
    tp = entry + dist * RR_STATIC if direction == 'long' else entry - dist * RR_STATIC
    risk = avail * RISK_PER_TRADE
    qty = risk / dist  # coin units
    return dict(entry=entry, sl=sl, tp=tp, dir=direction, qty=qty)

# ───────────────────────── WORKER ───────────────────────────

def worker(sym):
    p_prec, q_prec, min_qty, min_cost = market_info(sym)
    try:
        ex.set_leverage(LEVERAGE, sym)
    except Exception as e:
        logging.warning(f"{sym}: leverage set failed {e}")

    bal = ex.fetch_balance({'type': 'future'})
    avail = bal['free'].get('USDT') or bal['free'].get('BNFCR') or bal['free'].get('USDC')
    if not avail:
        logging.warning(f"{sym}: no free balance – thread exit")
        return
    logging.info(f"{sym}: free balance {avail:.2f}")

    in_pos = False
    orders = {}
    last_day = None
    trade_taken = False  # NEW FLAG: ensures max 1 trade each day

    while True:
        now = dt.datetime.utcnow()
        raw = ex.fetch_ohlcv(sym, TIMEFRAME, limit=500)
        df = pd.DataFrame(raw, columns=['ts','open','high','low','close','volume'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
        df.set_index('ts', inplace=True)
        day_df = df[df.index.date == now.date()]
        asia = day_df[day_df.index.map(get_session) == 'Asia']
        lon  = day_df[day_df.index.map(get_session) == 'London']
        kill = day_df[day_df.index.map(get_session) == 'KillZone']

        # reset at new UTC day
        if last_day and now.date() > last_day:
            in_pos, orders, trade_taken = False, {}, False
        last_day = now.date()

        # skip rest of day if already traded today
        if trade_taken:
            time.sleep(30)
            continue

        if in_pos:
            try:
                st = [ex.fetch_order(i, sym)['status'] for i in orders.values()]
                if any(s in ('closed', 'canceled') for s in st):
                    in_pos, orders = False, {}
            except Exception:
                pass
            time.sleep(10)
            continue

        if get_session(now) != 'KillZone':
            time.sleep(30)
            continue

        trade = build_trade(kill, detect_sweep(asia, lon), avail)
        if not trade:
            time.sleep(30)
            continue

        qty_raw = min(trade['qty'], avail * LEVERAGE / trade['entry'] * 0.98)
        qty = d_round(qty_raw, q_prec)
        notional = qty * trade['entry']
        if qty < min_qty or notional < min_cost:
            logging.info(f"{sym}: below exchange filter – skip setup")
            time.sleep(30)
            continue

        side = 'sell' if trade['dir'] == 'short' else 'buy'
        hedge = 'buy' if side == 'sell' else 'sell'
        logging.info(f"{sym}: sending market {side} {qty}")
        try:
            entry = ex.create_order(sym, 'MARKET', side.upper(), qty)
        except ccxt.InsufficientFunds:
            logging.error(f"{sym}: insufficient even after cap – skip")
            time.sleep(60)
            continue
        except Exception as e:
            logging.error(f"{sym}: order error {e}")
            time.sleep(60)
            continue

        tp = d_round(trade['tp'], p_prec)
        sl = d_round(trade['sl'], p_prec)
        try:
            tp_id = ex.create_order(sym, 'LIMIT', hedge.upper(), qty, tp,
                                    {'reduceOnly': True, 'timeInForce': 'GTC'})['id']
            sl_id = ex.create_order(sym, 'STOP_MARKET', hedge.upper(), qty, None,
                                    {'stopPrice': sl, 'reduceOnly': True})['id']
        except Exception as e:
            logging.error(f"{sym}: TP/SL attach error {e}")
        else:
            orders = {'tp': tp_id, 'sl': sl_id}
            in_pos = True
            trade_taken = True  # mark that we traded today
            logging.info(f"{sym}: entry={entry['price']} TP={tp} SL={sl}")
        time.sleep(10)

# ─────────────────────── Main ─────────────────────────────
if __name__ == '__main__':
    logging.info("Bot starting – one trade per symbol per UTC day")
    import threading
    for s in SYMBOLS:
        threading.Thread(target=worker, args=(s,), daemon=True).start()
    while True:
        time.sleep(60)
