#!/usr/bin/env python3
"""
live_bot.py – Liquidity‑sweep Kill‑Zone strategy for Binance USDT‑M Futures
Revision 23‑Apr‑2025  ➜  **Print current price for each symbol**
------------------------------------------------------------
* Now logs the **latest market price** at the start of each 5‑min cycle.
* Position size respects the 2 % account‑risk rule, one trade per UTC day.
"""
import time, datetime as dt, socket, logging
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import ccxt
import pandas as pd
import uuid
import boto3
from botocore.exceptions import ClientError

# DynamoDB setup
dynamodb = boto3.resource('dynamodb', region_name='us-west-1')  # adjust region
trades_table = dynamodb.Table('Trades')

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
RISK_PER_TRADE = 0.02       # 2 % of free wallet per trade
LEVERAGE       = 30
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
    return (int(m['precision']['price'] or 2),
            int(m['precision']['amount'] or 3),
            float(m['limits']['amount']['min'] or 0),
            float(m['limits']['cost']['min'] or 0))

def d_round(val, prec):
    try:
        return float(Decimal(val).quantize(Decimal(f'1e-{prec}'), ROUND_DOWN))
    except InvalidOperation:
        return val

def detect_sweep(a, l):
    if a.empty or l.empty: return None
    hi_lo = (a['high'].max(), a['low'].min(), l['high'].max(), l['low'].min())
    a_hi, a_lo, l_hi, l_lo = hi_lo
    if l_hi > a_hi and l_lo < a_lo: return 'both'
    if l_hi > a_hi: return 'high'
    if l_lo < a_lo: return 'low'
    return None

def build_trade(k_df, sweep, avail):
    if k_df.empty or sweep in (None,'both'): return None
    c0 = k_df.iloc[0]
    entry = c0['close']
    direction = 'short' if sweep=='high' else 'long'
    sl = c0['low']*0.999 if direction=='long' else c0['high']*1.001
    sl_diff = abs(entry-sl)
    tp = entry + sl_diff*RR_STATIC if direction=='long' else entry - sl_diff*RR_STATIC
    risk = avail * RISK_PER_TRADE
    qty_risk = risk / sl_diff
    qty_margin = risk * LEVERAGE / entry
    return dict(entry=entry,sl=sl,tp=tp,dir=direction,qty=min(qty_risk,qty_margin),sl_diff=sl_diff)

def write_trade_open(symbol, reason, entry_price, tp, sl, balance_start):
    trade_id = str(uuid.uuid4())
    open_time = dt.datetime.utcnow().isoformat()
    item = {
        'trade_id':    trade_id,
        'symbol':      symbol,
        'reason':      reason,
        'open_time':   open_time,
        'entry_price': Decimal(str(entry_price)),
        'tp':          Decimal(str(tp)),
        'sl':          Decimal(str(sl)),
        'balance_start': Decimal(str(balance_start)),
        # closing fields will come later:
        'closed':      False
    }
    try:
        trades_table.put_item(Item=item)
    except ClientError as e:
        logging.error(f"DynamoDB open write failed: {e}")
    return trade_id

def write_trade_close(trade_id, close_price, pnl, balance_end):
    close_time = dt.datetime.utcnow().isoformat()
    try:
        trades_table.update_item(
            Key={'trade_id': trade_id},
            UpdateExpression="""
                SET close_price = :cp,
                    close_time  = :ct,
                    realised_pnl = :p,
                    balance_end  = :be,
                    closed       = :cl
            """,
            ExpressionAttributeValues={
                ':cp': Decimal(str(close_price)),
                ':ct': close_time,
                ':p':  Decimal(str(pnl)),
                ':be': Decimal(str(balance_end)),
                ':cl': True
            }
        )
    except ClientError as e:
        logging.error(f"DynamoDB close write failed: {e}")


# ───────────────────────── WORKER ───────────────────────────
def worker(sym):
    p_prec,q_prec,min_qty,min_cost = market_info(sym)
    try: ex.set_leverage(LEVERAGE,sym)
    except Exception as e: logging.warning(f"{sym} leverage fail {e}")

    bal = ex.fetch_balance({'type':'future'})
    avail =  float(bal['info']['availableBalance'])
    if not avail:
        logging.warning(f"{sym}: no free balance")
        return
    logging.info(f"{sym}: free balance = {avail}")

    in_pos, orders, traded, last_day = False, {}, False, None
    while True:
        logging.info("---------------------------------------------")
        now = dt.datetime.utcnow()
        # __PRINT CURRENT PRICE__
        ticker = ex.fetch_ticker(sym)
        logging.info(f"{sym}: current price = {ticker['last']}")
        # load candles
        raw = ex.fetch_ohlcv(sym,TIMEFRAME,limit=500)
        df = pd.DataFrame(raw,columns=['ts','open','high','low','close','volume'])
        df['ts']=pd.to_datetime(df['ts'],unit='ms',utc=True); df.set_index('ts',inplace=True)
        day = df[df.index.date==now.date()]
        asia = day[day.index.map(get_session)=='Asia']
        lon  = day[day.index.map(get_session)=='London']
        kill = day[day.index.map(get_session)=='KillZone']

        if last_day and now.date()>last_day:
            in_pos,orders,traded=False,{},{False}
        last_day = now.date()

        if traded: time.sleep(30); continue
        if in_pos:
            try:
                sts=[ex.fetch_order(i,sym)['status'] for i in orders.values()]
                if any(s in ('closed', 'canceled') for s in sts):
                    # fetch final P&L and balance
                    balance_info = ex.fetch_balance({'type': 'future'})
                    balance_end = float(balance_info['info']['availableBalance'])
                    # assume you stored entry_price earlier in orders or tracking structure
                    closing_price = float(ex.fetch_ticker(sym)['last'])
                    realised_pnl = (closing_price - entry_price) * trade_qty_if_long_else_inverse

                    # write to DynamoDB
                    write_trade_close(
                        trade_id=orders.get('trade_id'),
                        close_price=closing_price,
                        pnl=realised_pnl,
                        balance_end=balance_end
                    )

                    # reset flags
                    in_pos, orders = False, {}
            except: pass
            time.sleep(10); continue
        if get_session(now)!='KillZone': time.sleep(30); continue

        trade = build_trade(kill,detect_sweep(asia,lon),avail)
        if not trade: time.sleep(30); continue
        qty = d_round(trade['qty'],q_prec)
        noti = qty*trade['entry']
        if qty<min_qty or noti<min_cost:
            logging.info(f"{sym}: below filters")
            time.sleep(30); continue

        side='sell' if trade['dir']=='short' else 'buy'
        hedge='buy' if side=='sell' else 'sell'
        logging.info(f"{sym}: {side} {qty} notional≈{noti:.2f}")
        try:
            entry = ex.create_order(sym,'MARKET',side.upper(),qty)
            entry_price = float(entry['price'])
            balance_start = avail  # free balance just before opening

            # log to DynamoDB
            trade_id = write_trade_open(
                symbol=sym,
                reason=f"Sweep {trade['dir']} at session {get_session(now)}",
                entry_price=entry_price,
                tp=tp,
                sl=sl,
                balance_start=balance_start
            )
            # store trade_id so you can reference it later
            orders['trade_id'] = trade_id
        except ccxt.InsufficientFunds:
            logging.error(f"{sym}: abort, no funds")
            time.sleep(60); continue
        except Exception as e:
            logging.error(f"{sym}: order err {e}")
            time.sleep(60); continue

        tp = d_round(trade['tp'],p_prec); sl = d_round(trade['sl'],p_prec)
        try:
            tp_id=ex.create_order(sym,'LIMIT',hedge.upper(),qty,tp,{'reduceOnly':True,'timeInForce':'GTC'})['id']
            sl_id=ex.create_order(sym,'STOP_MARKET',hedge.upper(),qty,None,{'stopPrice':sl,'reduceOnly':True})['id']
        except Exception as e:
            logging.error(f"{sym}: attach err {e}")
        else:
            orders={'tp':tp_id,'sl':sl_id}; in_pos,traded=True,True
            logging.info(f"{sym}: entry={entry['price']} TP={tp} SL={sl}")
        time.sleep(10)

# ─────────────────────── Main ─────────────────────────────
if __name__=='__main__':
    logging.info("Bot starting – one trade/day; 2% risk; logging price")
    import threading
    for s in SYMBOLS:
        threading.Thread(target=worker,args=(s,),daemon=True).start()
    while True: time.sleep(60)
