#!/usr/bin/env python3
"""
live_bot.py – Liquidity‑sweep Kill‑Zone strategy for Binance USDT‑M Futures
• Safe position‑sizing that respects available margin
• Robust error‑handling so a single symbol/thread cannot crash the bot
• Detailed logging captured by systemd‑journal + log file
"""
import os, time, datetime as dt
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import ccxt
import pandas as pd
import socket
import logging

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("/home/ec2-user/TradingBots/LiquidityTrading/live_bot.log"),
        logging.StreamHandler()
    ]
)

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
RISK_PER_TRADE = 0.02          # 2 % of wallet per setup
LEVERAGE       = 25            # account leverage setting
RR_STATIC      = 3.0           # take‑profit multiple
TIMEFRAME      = '5m'
SYMBOLS = [
    'SOL/USDT', 'XRP/USDT', 'LINK/USDT',
    'BTC/USDT', 'ETH/USDT', 'LTC/USDT',
]
TESTNET = False                # flip to True for testnet

API_KEY    = "rdpvKsuXdhdNXHPAM7XgZ6sfCQXLXBvfNFLMEQZNqaeHilqbIREar8LXWj65x8z8"
API_SECRET = "jciGO3TOYa5CSHVS1qWG2H0gV7hCtiRyC8eM3x5x3AqiRN2iXg91Z3uapXDsieLx"

host = socket.gethostname(); ip = socket.gethostbyname(host)
logging.info(f"Host {host} | IP {ip}")

# ──────────────────────────────────────────────
# Exchange helper
# ──────────────────────────────────────────────

def make_exchange():
    base = 'https://testnet.binancefuture.com' if TESTNET else 'https://fapi.binance.com'
    ex = ccxt.binanceusdm({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'urls': {'api': {'public': base, 'private': base}},
    })
    ex.load_markets()
    return ex

ex = make_exchange()

# ──────────────────────────────────────────────
# Utility helpers
# ──────────────────────────────────────────────

def get_session(ts: dt.datetime):
    h = ts.hour
    if 0 <= h < 5:   return 'Asia'
    if 5 <= h < 11:  return 'London'
    if 14 <= h < 17: return 'KillZone'
    return 'Off'

def get_precision(symbol):
    info = ex.market(symbol)
    price_prec = info['precision'].get('price') or 2
    qty_prec   = info['precision'].get('amount') or 3
    logging.info(f"{symbol} precision → price:{price_prec}, qty:{qty_prec}")
    return int(price_prec), int(qty_prec)

def d_round(value, prec):
    try:
        return float(Decimal(value).quantize(Decimal(f'1e-{prec}'), rounding=ROUND_DOWN))
    except InvalidOperation:
        return value

def detect_sweep(asian, london):
    if asian.empty or london.empty:
        return None
    a_hi, a_lo = asian['high'].max(), asian['low'].min()
    l_hi, l_lo = london['high'].max(), london['low'].min()
    if l_hi > a_hi and l_lo < a_lo: return 'both'
    if l_hi > a_hi: return 'high'
    if l_lo < a_lo: return 'low'
    return None

# ──────────────────────────────────────────────
# Trade sizing & struct builder
# ──────────────────────────────────────────────

def build_trade(kill_df, sweep, wallet):
    if kill_df.empty or sweep in (None, 'both'):
        return None
    c0          = kill_df.iloc[0]
    entry_price = c0['close']
    direction   = 'short' if sweep == 'high' else 'long'
    sl          = c0['low']*0.999 if direction=='long' else c0['high']*1.001
    dist        = abs(entry_price - sl)
    if dist <= 0: return None
    tp          = entry_price + dist*RR_STATIC if direction=='long' else entry_price - dist*RR_STATIC
    risk_usdt   = wallet * RISK_PER_TRADE
    qty_calc    = risk_usdt / dist      # coin qty
    return dict(entry=entry_price, sl=sl, tp=tp, dir=direction, qty_calc=qty_calc)

# ──────────────────────────────────────────────
# Worker thread
# ──────────────────────────────────────────────

def worker(symbol):
    p_prec, q_prec = get_precision(symbol)

    bal   = ex.fetch_balance({'type': 'future'})['total']
    wallet = bal.get('USDC')
    if not wallet:
        logging.warning(f"{symbol}: No futures balance – skipping")
        return

    logging.info(f"{symbol}: thread started with wallet≈{wallet:.2f}")
    in_pos, orders, last_day = False, {}, None

    while True:
        now = dt.datetime.utcnow()
        candles = ex.fetch_ohlcv(symbol, TIMEFRAME, limit=500)
        df = pd.DataFrame(candles, columns=['ts','open','high','low','close','vol'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True); df.set_index('ts', inplace=True)
        today = now.date(); day_df = df[df.index.date == today]
        asia = day_df[day_df.index.map(get_session)=='Asia']
        lon  = day_df[day_df.index.map(get_session)=='London']
        kill = day_df[day_df.index.map(get_session)=='KillZone']

        # reset each UTC day
        if last_day and today>last_day: in_pos, orders = False, {}
        last_day = today

        if in_pos:
            try:
                statuses=[ex.fetch_order(i,symbol)['status'] for i in orders.values()]
                if any(s in ('closed','canceled') for s in statuses): in_pos=False; orders={}
            except Exception as e:
                logging.error(f"{symbol}: poll err {e}")
            time.sleep(10); continue

        if get_session(now)!='KillZone': time.sleep(30); continue

        trade=build_trade(kill, detect_sweep(asia, lon), wallet)
        if not trade: time.sleep(30); continue

        # sizing cap
        max_qty = wallet*LEVERAGE/trade['entry']*0.98
        qty_raw = min(trade['qty_calc'], max_qty)
        qty     = d_round(qty_raw, q_prec)
        if qty<=0:
            logging.info(f"{symbol}: qty≈0, skip")
            time.sleep(30); continue

        side  = 'sell' if trade['dir']=='short' else 'buy'
        hedge = 'buy' if side=='sell' else 'sell'
        logging.info(f"{symbol}: market {side} {qty}")
        try:
            entry=ex.create_order(symbol,'MARKET',side.upper(),qty)
        except ccxt.InsufficientFunds:
            logging.warning(f"{symbol}: insufficient margin, skipping")
            time.sleep(60); continue
        except Exception as e:
            logging.error(f"{symbol}: order err {e}"); time.sleep(60); continue

        tp=d_round(trade['tp'],p_prec); sl=d_round(trade['sl'],p_prec)
        try:
            tp_id=ex.create_order(symbol,'LIMIT',hedge.upper(),qty,tp,{'reduceOnly':True,'timeInForce':'GTC'})['id']
            sl_id=ex.create_order(symbol,'STOP_MARKET',hedge.upper(),qty,None,{'stopPrice':sl,'reduceOnly':True})['id']
        except Exception as e:
            logging.error(f"{symbol}: failed attach TPSL {e}")
        else:
            orders={'tp':tp_id,'sl':sl_id}; in_pos=True
            logging.info(f"{symbol}: entry={entry['price']} TP={tp} SL={sl}")
        time.sleep(10)

# ──────────────────────────────────────────────
# Launcher
# ──────────────────────────────────────────────
if __name__=='__main__':
    logging.info("Bot starting… Ctrl‑C to stop")
    import threading
    for sym in SYMBOLS:
        threading.Thread(target=worker,args=(sym,),daemon=True).start()
    while True: time.sleep(60)
