#!/usr/bin/env python3
"""
live_bot.py  –  Liquidity‑sweep Kill‑Zone strategy, Binance USDT‑M Futures
"""
import os, time, datetime as dt
from decimal import Decimal, ROUND_DOWN
import ccxt
import pandas as pd
import socket

# ───────────────────────────────────────────────────────────────────────────
# 0. CONFIG
# ───────────────────────────────────────────────────────────────────────────
ACCOUNT_SIZE_START = 20.0          # starting equity in USDT
RISK_PER_TRADE     = 0.02             # fraction of equity to risk
LEVERAGE           = 25               # x‑leverage
RR_STATIC          = 3.0              # reward:risk
TIMEFRAME          = '5m'
SYMBOLS            = ['SOL/USDT', 'XRP/USDT', 'LINK/USDT', 'BTC/USDT', 'ETH/USDT', 'LTC/USDT']   # trade universe
TESTNET            = False            # <-- flip to True for testnet

API_KEY    = os.getenv("BINANCE_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")
# API_KEY    = "rdpvKsuXdhdNXHPAM7XgZ6sfCQXLXBvfNFLMEQZNqaeHilqbIREar8LXWj65x8z8"
# API_SECRET = "jciGO3TOYa5CSHVS1qWG2H0gV7hCtiRyC8eM3x5x3AqiRN2iXg91Z3uapXDsieLx"
if not API_KEY or not API_SECRET:
    raise EnvironmentError("Set BINANCE_KEY and BINANCE_SECRET in env!")

# Python Program to Get IP Address
hostname = socket.gethostname()
IPAddr = socket.gethostbyname(hostname)
print("Your Computer Name is:" + hostname)
print("Your Computer IP Address is:" + IPAddr)
# ───────────────────────────────────────────────────────────────────────────
# 1. EXCHANGE CONSTRUCTOR
# ───────────────────────────────────────────────────────────────────────────
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
    exchange.load_markets()   # <<< ADD THIS LINE
    return exchange

ex = make_exchange()
balance = ex.fetch_balance({'type': 'future'})
usdt_balance = balance['total']['USDT']
print("Account Balance: ", usdt_balance)

# ───────────────────────────────────────────────────────────────────────────
# 2. HELPERS • sessions, sweeps, qty/price rounding
# ───────────────────────────────────────────────────────────────────────────
def get_session(ts: dt.datetime):
    h = ts.hour
    if 0 <= h < 5:   return 'Asia'
    if 5 <= h < 11:  return 'London'
    if 14 <= h < 17: return 'KillZone'
    return 'Off'

def get_precision(symbol):
    info = ex.market(symbol)
    return info['precision']['price'], info['precision']['amount']

def round_price(p, prec):  # «prec» is number of decimals
    q = Decimal(p)
    return float(q.quantize(Decimal('1e-{0}'.format(prec)), rounding=ROUND_DOWN))

def round_qty(q, prec):
    qd = Decimal(q)
    return float(qd.quantize(Decimal('1e-{0}'.format(prec)), rounding=ROUND_DOWN))

def detect_sweep(asia_df, london_df):
    if asia_df.empty or london_df.empty:
        return None
    asia_hi, asia_lo = asia_df['high'].max(), asia_df['low'].min()
    lon_hi,  lon_lo  = london_df['high'].max(),  london_df['low'].min()
    if lon_hi > asia_hi and lon_lo < asia_lo: return 'both'
    if lon_hi > asia_hi:  return 'high'
    if lon_lo < asia_lo:  return 'low'
    return None

# ───────────────────────────────────────────────────────────────────────────
# 3. MAIN LOOP PER SYMBOL
# ───────────────────────────────────────────────────────────────────────────
def trade_symbol(symbol):
    price_prec, qty_prec = get_precision(symbol)
    balance = ex.fetch_balance({'type': 'future'})
    equity = balance['total']['USDT']
    print(f"{symbol} | {dt.datetime.utcnow()} | Starting with {equity:.2f} USDT available.")
    in_position = False
    order_ids = {}
    last_trade_day = None

    while True:
        now = dt.datetime.utcnow()
        df = ex.fetch_ohlcv(symbol, TIMEFRAME, limit=500)
        df = pd.DataFrame(df, columns=['ts','open','high','low','close','vol'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
        df.set_index('ts', inplace=True)

        # ───────── group today's sessions
        today = now.date()
        today_df = df[df.index.date == today]
        asia_df   = today_df[today_df.index.map(get_session) == 'Asia']
        london_df = today_df[today_df.index.map(get_session) == 'London']
        kill_df   = today_df[today_df.index.map(get_session) == 'KillZone']

        # reset at new UTC day
        if last_trade_day and today > last_trade_day:
            in_position, order_ids = False, {}
        last_trade_day = today

        # ───────── check for active position
        if in_position:
            # poll order status; if BOTH TP & SL are done, we’re flat
            statuses = [ex.fetch_order(oid, symbol)['status'] for oid in order_ids.values()]
            if any(s in ('closed','canceled') for s in statuses):
                in_position, order_ids = False, {}
            time.sleep(10)
            continue

        # ───────── entry logic only inside Kill‑Zone
        if get_session(now) != 'KillZone':
            time.sleep(30)
            continue

        sweep_side = detect_sweep(asia_df, london_df)
        trade = execute_killzone_trade(kill_df, sweep_side, equity)
        if not trade:       # no setup
            time.sleep(30)
            continue

        # ─────── duplicate position‑size maths for futures contracts
        risk_amount = equity * RISK_PER_TRADE
        distance    = abs(trade['entry'] - trade['sl'])
        contracts   = risk_amount / distance              # USDT value
        contracts  *= LEVERAGE / trade['entry']           # convert → qty
        qty         = round_qty(contracts, qty_prec)
        if qty <= 0:
            print("Qty rounds to zero – skip.")
            time.sleep(30);  continue

        side    = 'sell' if trade['direction']=='short' else 'buy'
        hedge   = 'buy'  if side=='sell' else 'sell'

        # ─────── place MARKET entry
        print(f"{symbol} | {now} placing market {side} {qty}")
        entry = ex.create_order(symbol, 'MARKET', side.upper(), qty)

        # ─────── immediately attach TP & SL (reduce‑only)
        tp = round_price(trade['tp'], price_prec)
        sl = round_price(trade['sl'], price_prec)

        tp_ord = ex.create_order(
            symbol, 'LIMIT', hedge.upper(), qty, tp,
            {'reduceOnly': True, 'timeInForce': 'GTC'}
        )
        sl_ord = ex.create_order(
            symbol, 'STOP_MARKET', hedge.upper(), qty, None,
            {'stopPrice': sl, 'reduceOnly': True, 'closePosition': False}
        )

        order_ids = {'tp': tp_ord['id'], 'sl': sl_ord['id']}
        in_position = True
        print(f"--> entry={entry['price']}  TP={tp}  SL={sl}")
        time.sleep(10)

# ───────────────────────────────────────────────────────────────────────────
# 4. FIRE UP ONE SYMBOL PER THREAD (simplest form)
# ───────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import threading
    print("Bot starting…  ctrl‑c to stop.")
    for sym in SYMBOLS:
        t = threading.Thread(target=trade_symbol, args=(sym,), daemon=True)
        t.start()
    while True:
        time.sleep(60)
