#!/usr/bin/env python3
"""
live_bot.py – Liquidity‑sweep Kill‑Zone strategy for Binance USDT‑M Futures
Revision 23‑Apr‑2025  ➜  **Correct risk‑based sizing**
------------------------------------------------------------
* Position size now respects the *2 % account‑risk rule* exactly:
  - `risk_usd   = free_balance × RISK_PER_TRADE`
  - `qty_risk   = risk_usd / sl_distance`
  - `qty_margin = risk_usd * LEVERAGE / entry_price`  (so required margin ≤ 2 %)
  - `qty        = min(qty_risk, qty_margin)`
* Keeps “one trade per symbol per UTC‑day” logic.
* Fixes dataframe column mismatch.
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
RISK_PER_TRADE = 0.02       # 2 % of *free* wallet per trade
LEVERAGE       = 25
RR_STATIC      = 3.0
TIMEFRAME      = '5m'
SYMBOLS        = ['SOL/USDT','XRP/USDT','LINK/USDT','BTC/USDT','ETH/USDT','LTC/USDT']
TESTNET        = False
API_KEY        = "rdpvKsuXdhdNXHPAM7XgZ6sfCQXLXBvfNFLMEQZNqaeHilqbIREar8LXWj65x8z8"
API_SECRET     = "jciGO3TOYa5CSHVS1qWG2H0gV7hCtiRyC8eM3x5x3AqiRN2iXg91Z3uapXDsieLx"

logging.info(f"Running on {socket.gethostname()}")

# ─────────────────────── Exchange init ──────────────────────

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

# ───────────────────────── HELPERS ──────────────────────────

def get_session(ts: dt.datetime):
    h = ts.hour
    return 'Asia' if h < 5 else 'London' if h < 11 else 'KillZone' if 14 <= h < 17 else 'Off'

def market_info(sym):
    m = ex.market(sym)
    price_prec = m['precision']['price'] or 2
    qty_prec   = m['precision']['amount'] or 3
    min_qty    = m['limits']['amount']['min'] or 0.0
    min_cost   = m['limits']['cost']['min']   or 0.0
    return int(price_prec), int(qty_prec), float(min_qty), float(min_cost)

def d_round(val, prec):
    try:
        return float(Decimal(val).quantize(Decimal(f'1e-{prec}'), ROUND_DOWN))
    except InvalidOperation:
        return val

def detect_sweep(asia: pd.DataFrame, lon: pd.DataFrame):
    if asia.empty or lon.empty:
        return None
    a_hi, a_lo = asia['high'].max(), asia['low'].min()
    l_hi, l_lo = lon['high'].max(), lon['low'].min()
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
    sl_diff = abs(entry - sl)
    tp = entry + sl_diff * RR_STATIC if direction == 'long' else entry - sl_diff * RR_STATIC
    risk_usd = avail * RISK_PER_TRADE
    qty_risk = risk_usd / sl_diff
    qty_margin = risk_usd * LEVERAGE / entry  # ensures initial margin ≤ 2 %
    qty = min(qty_risk, qty_margin)
    return dict(entry=entry, sl=sl, tp=tp, dir=direction, qty=qty, sl_diff=sl_diff)

# ───────────────────────── WORKER ───────────────────────────

def worker(sym):
    p_prec, q_prec, min_qty, min_cost = market_info(sym)
    try:
        ex.set_leverage(LEVERAGE, sym)
    except Exception as e:
        logging.warning(f"{sym}: leverage set failed {e}")

    bal = ex.fetch_balance({'type': 'future'})
    free = bal['free'].get('USDT') or bal['free'].get('BNFCR') or bal['free'].get('USDC')
    if not free:
        logging.warning(f"{sym}: no free balance – exit thread")
        return
    logging.info(f"{sym}: free balance = {free:.2f} USDT")

    in_pos, orders, trade_taken, last_day = False, {}, False, None

    while True:
        now = dt.datetime.utcnow()
        raw = ex.fetch_ohlcv(sym, TIMEFRAME, limit=500)
        df = pd.DataFrame(raw, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
        df.set_index('ts', inplace=True)
        day_df = df[df.index.date == now.date()]
        asia   = day_df[day_df.index.map(get_session) == 'Asia']
        lon    = day_df[day_df.index.map(get_session) == 'London']
        kill   = day_df[day_df.index.map(get_session) == 'KillZone']

        if last_day and now.date() > last_day:
            in_pos, orders, trade_taken = False, {}, False
        last_day = now.date()

        if trade_taken:
            time.sleep(30); continue

        if in_pos:
            try:
                statuses = [ex.fetch_order(i, sym)['status'] for i in orders.values()]
                if any(s in ('closed', 'canceled') for s in statuses):
                    in_pos, orders = False, {}
            except Exception:
                pass
            time.sleep(10); continue

        if get_session(now) != 'KillZone':
            time.sleep(30); continue

        trade = build_trade(kill, detect_sweep(asia, lon), free)
        if not trade:
            time.sleep(30); continue

        qty = d_round(trade['qty'], q_prec)
        notional = qty * trade['entry']
        if qty < min_qty or notional < min_cost:
            logging.info(f"{sym}: below Binance filters – skip")
            time.sleep(30); continue

        side  = 'sell' if trade['dir'] == 'short' else 'buy'
        hedge = 'buy'  if side == 'sell' else 'sell'
        logging.info(f"{sym}: send {side} qty={qty} notional≈{notional:.2f}")
        try:
            entry = ex.create_order(sym, 'MARKET', side.upper(), qty)
        except ccxt.InsufficientFunds:
            logging.error(f"{sym}: still insufficient funds – abort setup")
            time.sleep(60); continue
        except Exception as e:
            logging.error(f"{sym}: create_order error {e}")
            time.sleep(60); continue

        tp = d_round(trade['tp'], p_prec); sl = d_round(trade['sl'], p_prec)
        try:
            tp_id = ex.create_order(sym, 'LIMIT', hedge.upper(), qty, tp,
                                    {'reduceOnly': True, 'timeInForce': 'GTC'})['id']
            sl_id = ex.create_order(sym, 'STOP_MARKET', hedge.upper(), qty, None,
                                    {'stopPrice': sl, 'reduceOnly': True})['id']
        except Exception as e:
            logging.error(f"{sym}: attach TP/SL err {e}")
        else:
            orders = {'tp': tp_id, 'sl': sl_id}; in_pos = True; trade_taken = True
            logging.info(f"{sym}: entry={entry['price']} TP={tp} SL={sl}")
        time.sleep(10)

# ─────────────────────── Main ─────────────────────────────
if __name__ == '__main__':
    logging.info("Bot starting – one trade per symbol per UTC day · 2 % risk")
    import threading
    for s in SYMBOLS:
        threading.Thread(target=worker, args=(s,), daemon=True).start()
    while True:
        time.sleep(60)
