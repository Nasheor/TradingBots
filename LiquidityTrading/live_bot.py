#!/usr/bin/env python3
"""
live_bot.py  –  Liquidity‑sweep Kill‑Zone strategy, Binance USDT‑M Futures
"""
import os, time, datetime as dt
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import ccxt
import pandas as pd
import socket
import logging

# Set up basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("/home/ec2-user/TradingBots/LiquidityTrading/live_bot.log"),
        logging.StreamHandler()
    ]
)

# ──── CONFIG ────
ACCOUNT_SIZE_START = 20.0
RISK_PER_TRADE     = 0.02
LEVERAGE           = 25
RR_STATIC          = 3.0
TIMEFRAME          = '5m'
SYMBOLS            = ['SOL/USDT', 'XRP/USDT', 'LINK/USDT', 'BTC/USDT', 'ETH/USDT', 'LTC/USDT']
TESTNET            = False

API_KEY    = "rdpvKsuXdhdNXHPAM7XgZ6sfCQXLXBvfNFLMEQZNqaeHilqbIREar8LXWj65x8z8"
API_SECRET = "jciGO3TOYa5CSHVS1qWG2H0gV7hCtiRyC8eM3x5x3AqiRN2iXg91Z3uapXDsieLx"

hostname = socket.gethostname()
IPAddr = socket.gethostbyname(hostname)
logging.info("Your Computer Name is: " + hostname)
logging.info("Your Computer IP Address is: " + IPAddr)

# ──── EXCHANGE ────

def make_exchange():
    url = 'https://fapi.binance.com'
    if TESTNET:
        url = 'https://testnet.binancefuture.com'
    exchange = ccxt.binanceusdm({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'urls': {'api': {'public': url, 'private': url}}
    })
    exchange.load_markets()
    return exchange

ex = make_exchange()

# ──── HELPERS ────

def get_session(ts: dt.datetime):
    h = ts.hour
    if 0 <= h < 5:   return 'Asia'
    if 5 <= h < 11:  return 'London'
    if 14 <= h < 17: return 'KillZone'
    return 'Off'

def get_precision(symbol):
    info = ex.market(symbol)
    price_prec = info['precision'].get('price')
    qty_prec = info['precision'].get('amount')
    if price_prec is None:
        price_prec = 2
    if qty_prec is None:
        qty_prec = 3
    logging.info(f"Precision for {symbol} → price: {price_prec}, qty: {qty_prec}")
    return price_prec, qty_prec

def round_price(p, prec):
    try:
        q = Decimal(p)
        return float(q.quantize(Decimal(f'1e-{int(prec)}'), rounding=ROUND_DOWN))
    except (InvalidOperation, TypeError, ValueError):
        logging.error(f"Invalid price precision value: {prec}")
        return p

def round_qty(q, prec):
    try:
        qd = Decimal(q)
        return float(qd.quantize(Decimal(f'1e-{int(prec)}'), rounding=ROUND_DOWN))
    except (InvalidOperation, TypeError, ValueError):
        logging.error(f"Invalid quantity precision value: {prec}")
        return q

def detect_sweep(asia_df, london_df):
    if asia_df.empty or london_df.empty:
        return None
    asia_hi, asia_lo = asia_df['high'].max(), asia_df['low'].min()
    lon_hi,  lon_lo  = london_df['high'].max(),  london_df['low'].min()
    if lon_hi > asia_hi and lon_lo < asia_lo: return 'both'
    if lon_hi > asia_hi:  return 'high'
    if lon_lo < asia_lo:  return 'low'
    return None

def execute_killzone_trade(killzone_df, sweep_side, account_balance):
    if killzone_df.empty or sweep_side is None or sweep_side == 'both':
        return None

    first_candle = killzone_df.iloc[0]
    entry_time = first_candle.name
    entry_price = first_candle['close']

    direction = 'short' if sweep_side == 'high' else 'long'
    sl = first_candle['low'] * 0.999 if direction == 'long' else first_candle['high'] * 1.001
    distance = abs(entry_price - sl)
    if distance <= 0:
        return None

    tp = entry_price + distance * RR_STATIC if direction == 'long' else entry_price - distance * RR_STATIC
    risk_amount = account_balance * RISK_PER_TRADE
    position_size = risk_amount / distance

    return {
        'entry_time': entry_time,
        'direction': direction,
        'entry': entry_price,
        'sl': sl,
        'tp': tp,
        'distance': distance,
        'position_size': position_size
    }

# ──── MAIN LOOP ────

def trade_symbol(symbol):
    price_prec, qty_prec = get_precision(symbol)

    balance = ex.fetch_balance({'type': 'future'})
    equity = balance['total'].get('USDT') or balance['total'].get('BNFCR') or balance['total'].get('USDC')

    if not equity:
        logging.warning(f"No balance found for {symbol}. Skipping.")
        return

    logging.info(f"{symbol} | {dt.datetime.utcnow()} | Starting with {equity:.2f} USDT-equivalent available.")

    in_position = False
    order_ids = {}
    last_trade_day = None

    while True:
        now = dt.datetime.utcnow()
        df = ex.fetch_ohlcv(symbol, TIMEFRAME, limit=500)
        df = pd.DataFrame(df, columns=['ts','open','high','low','close','vol'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
        df.set_index('ts', inplace=True)

        today = now.date()
        today_df = df[df.index.date == today]
        asia_df   = today_df[today_df.index.map(get_session) == 'Asia']
        london_df = today_df[today_df.index.map(get_session) == 'London']
        kill_df   = today_df[today_df.index.map(get_session) == 'KillZone']

        if last_trade_day and today > last_trade_day:
            in_position, order_ids = False, {}
        last_trade_day = today

        if in_position:
            statuses = [ex.fetch_order(oid, symbol)['status'] for oid in order_ids.values()]
            if any(s in ('closed','canceled') for s in statuses):
                in_position, order_ids = False, {}
            time.sleep(10)
            continue

        if get_session(now) != 'KillZone':
            time.sleep(30)
            continue

        sweep_side = detect_sweep(asia_df, london_df)
        trade = execute_killzone_trade(kill_df, sweep_side, equity)
        if not trade:
            time.sleep(30)
            continue

        risk_amount = equity * RISK_PER_TRADE
        distance = abs(trade['entry'] - trade['sl'])
        contracts = risk_amount / distance
        contracts *= LEVERAGE / trade['entry']
        qty = round_qty(contracts, qty_prec)

        if qty <= 0:
            logging.info("Qty rounds to zero – skip.")
            time.sleep(30)
            continue

        side = 'sell' if trade['direction'] == 'short' else 'buy'
        hedge = 'buy' if side == 'sell' else 'sell'

        logging.info(f"{symbol} | {now} placing market {side} {qty}")
        entry = ex.create_order(symbol, 'MARKET', side.upper(), qty)

        tp = round_price(trade['tp'], price_prec)
        sl = round_price(trade['sl'], price_prec)

        tp_ord = ex.create_order(symbol, 'LIMIT', hedge.upper(), qty, tp, {'reduceOnly': True, 'timeInForce': 'GTC'})
        sl_ord = ex.create_order(symbol, 'STOP_MARKET', hedge.upper(), qty, None, {'stopPrice': sl, 'reduceOnly': True, 'closePosition': False})

        order_ids = {'tp': tp_ord['id'], 'sl': sl_ord['id']}
        in_position = True
        logging.info(f"--> entry={entry['price']}  TP={tp}  SL={sl}")
        time.sleep(10)

# ──── THREAD RUNNER ────
if __name__ == '__main__':
    import threading
    logging.info("Bot starting…  ctrl‑c to stop.")
    for sym in SYMBOLS:
        t = threading.Thread(target=trade_symbol, args=(sym,), daemon=True)
        t.start()
    while True:
        time.sleep(60)
