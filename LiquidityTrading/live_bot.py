#!/usr/bin/env python3
"""
live_bot.py  –  Liquidity‑sweep Kill‑Zone strategy, Binance USDT‑M Futures
This version adds safe sizing that respects available margin and
wraps order placement in try/except so the thread never crashes on
`InsufficientFunds`.
"""
import os, time, datetime as dt
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import ccxt
import pandas as pd
import socket
import logging

# ────────────────────────────────────────────────────────────────
# Logging
# ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("/home/ec2-user/TradingBots/LiquidityTrading/live_bot.log"),
        logging.StreamHandler()
    ]
)

# ────────────────────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────────────────────
ACCOUNT_SIZE_START = 20.0          # not used for live sizing now
RISK_PER_TRADE     = 0.02          # 2 % of wallet balance per trade
LEVERAGE           = 25            # x‑leverage (affects required margin)
RR_STATIC          = 3.0           # reward : risk
TIMEFRAME          = '5m'
SYMBOLS            = [
    'SOL/USDT', 'XRP/USDT', 'LINK/USDT',
    'BTC/USDT', 'ETH/USDT', 'LTC/USDT'
]
TESTNET            = False         # flip to True for testnet

API_KEY    = "rdpvKsuXdhdNXHPAM7XgZ6sfCQXLXBvfNFLMEQZNqaeHilqbIREar8LXWj65x8z8"
API_SECRET = "jciGO3TOYa5CSHVS1qWG2H0gV7hCtiRyC8eM3x5x3AqiRN2iXg91Z3uapXDsieLx"

hostname = socket.gethostname()
IPAddr   = socket.gethostbyname(hostname)
logging.info(f"Host: {hostname}  |  IP: {IPAddr}")

# ────────────────────────────────────────────────────────────────
# Exchange helper
# ────────────────────────────────────────────────────────────────

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

# ────────────────────────────────────────────────────────────────
# Utility functions
# ────────────────────────────────────────────────────────────────

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
    logging.info(f"Precision for {symbol} → price: {price_prec}, qty: {qty_prec}")
    return int(price_prec), int(qty_prec)

def round_price(p, prec):
    try:
        return float(Decimal(p).quantize(Decimal(f'1e-{prec}'), rounding=ROUND_DOWN))
    except InvalidOperation:
        logging.error(f"round_price InvalidOperation: p={p} prec={prec}")
        return p

def round_qty(q, prec):
    try:
        return float(Decimal(q).quantize(Decimal(f'1e-{prec}'), rounding=ROUND_DOWN))
    except InvalidOperation:
        logging.error(f"round_qty InvalidOperation: q={q} prec={prec}")
        return q

def detect_sweep(asia_df, london_df):
    if asia_df.empty or london_df.empty:
        return None
    asia_hi, asia_lo = asia_df['high'].max(), asia_df['low'].min()
    lon_hi,  lon_lo  = london_df['high'].max(),  london_df['low'].min()
    if lon_hi > asia_hi and lon_lo < asia_lo:
        return 'both'
    if lon_hi > asia_hi:
        return 'high'
    if lon_lo < asia_lo:
        return 'low'
    return None

def execute_killzone_trade(killzone_df, sweep_side, account_balance):
    if killzone_df.empty or sweep_side is None or sweep_side == 'both':
        return None

    first_candle = killzone_df.iloc[0]
    entry_time   = first_candle.name
    entry_price  = first_candle['close']

    direction  = 'short' if sweep_side == 'high' else 'long'
    sl         = first_candle['low'] * 0.999 if direction == 'long' else first_candle['high'] * 1.001
    distance   = abs(entry_price - sl)
    if distance <= 0:
        return None

    tp         = entry_price + distance * RR_STATIC if direction == 'long' else entry_price - distance * RR_STATIC
    risk_usdt  = account_balance * RISK_PER_TRADE
    position_q = risk_usdt / distance  # qty in coin units

    return {
        'entry_time': entry_time,
        'direction' : direction,
        'entry'     : entry_price,
        'sl'        : sl,
        'tp'        : tp,
        'distance'  : distance,
        'qty_calc'  : position_q,
    }

# ────────────────────────────────────────────────────────────────
# Core worker per symbol
# ────────────────────────────────────────────────────────────────

def trade_symbol(symbol):
    price_prec, qty_prec = get_precision(symbol)

    balance = ex.fetch_balance({'type': 'future'})
    equity  = balance['total'].get('USDT') or balance['total'].get('BNFCR') or balance['total'].get('USDC')
    if not equity:
        logging.warning(f"{symbol}: No futures balance detected – skipping thread")
        return

    logging.info(f"{symbol}: Starting thread with equity ≈ {equity:.2f} USDT")

    in_position, order_ids, last_trade_day = False, {}, None

    while True:
        now = dt.datetime.utcnow()
        df  = ex.fetch_ohlcv(symbol, TIMEFRAME, limit=500)
        df  = pd.DataFrame(df, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
        df.set_index('ts', inplace=True)

        today     = now.date()
        today_df  = df[df.index.date == today]
        asia_df   = today_df[today_df.index.map(get_session) == 'Asia']
        london_df = today_df[today_df.index.map(get_session) == 'London']
        kill_df   = today_df[today_df.index.map(get_session) == 'KillZone']

        # daily reset
        if last_trade_day and today > last_trade_day:
            in_position, order_ids = False, {}
        last_trade_day = today

        # manage open
        if in_position:
            try:
                statuses = [ex.fetch_order(oid, symbol)['status'] for oid in order_ids.values()]
                if any(s in ('closed', 'canceled') for s in statuses):
                    in_position, order_ids = False, {}
            except Exception as e:
                logging.error(f"{symbol}: error polling orders – {e}")
            time.sleep(10)
            continue

        # wait for kill‑zone
        if get_session(now) != 'KillZone':
            time.sleep(30)
            continue

        trade = execute_killzone_trade(kill_df, detect_sweep(asia_df, london_df), equity)
        if not trade:
            time.sleep(30)
            continue

        # ---------------- position sizing ----------------
        qty_calc = trade['qty_calc']
        max_qty  = equity * LEVERAGE / trade['entry'] * 0.98  # 98 % of margin cap
        qty_raw  = min(qty_calc, max_qty)
        qty      = round_qty(qty_raw, qty_prec)
        if qty <= 0:
            logging.info(f"{symbol}: qty rounds to zero – skip setup")
            time.sleep(30)
            continue

        side  = 'sell' if trade['direction'] == 'short' else 'buy'
        hedge = 'buy'  if side == 'sell' else 'sell'

        logging
