#!/usr/bin/env python3
import time
import datetime as dt
import logging
import pandas as pd
from decimal      import Decimal, ROUND_UP
from config       import TIMEFRAME, LEVERAGE
from exchange     import EX, fetch_balance, fetch_price, fetch_ohlcv
from sessions     import get_session, detect_sweep
from strategy     import build_trade, d_round
from orders       import set_leverage, place_entry, attach_tp_sl
from dynamo       import write_trade_open, write_trade_close

def worker(symbol):
    # ───────── initialize symbol thread ─────────
    set_leverage(symbol, LEVERAGE)
    avail = fetch_balance()
    logging.info(f"{symbol}: available balance = {avail:.2f} USDT")

    # ───────── load symbol-specific Binance filters ─────────
    market = EX.market(symbol)
    p_prec = int(market['precision'].get('price', 2) or 2)
    q_prec = int(market['precision'].get('amount', 3) or 3)
    min_qty = float(market['limits']['amount']['min'] or 0.0)
    min_cost = float(market['limits']['cost']['min'] or 0.0)

    in_pos               = False
    orders               = {}
    trade_taken_session  = False
    last_session         = None

    while True:
        now     = dt.datetime.utcnow()
        session = get_session(now)

        # ───────── reset once per session change ─────────
        if session != last_session:
            trade_taken_session = False
            last_session        = session

        # ───────── poll existing position ─────────
        if in_pos:
            try:
                statuses = []
                for side in ('tp', 'sl'):
                    oid = orders.get(side)
                    # if attach returned a full order dict, pull out its id
                    if isinstance(oid, dict) and 'id' in oid:
                        oid = oid['id']
                    # only proceed if oid is now a non-empty string or int
                    if not isinstance(oid, (str, int)) or not oid:
                        continue
                    statuses.append(EX.fetch_order(oid, symbol)['status'])
                if any(s in ('closed', 'canceled') for s in statuses):
                    # Trade closed: compute PnL and log
                    exit_price = float(fetch_price(symbol))
                    entry_price = orders['entry_price']
                    qty         = orders['qty']
                    direction   = orders['dir']
                    if direction == 'long':
                        realised_pnl = (exit_price - entry_price) * qty
                    else:
                        realised_pnl = (entry_price - exit_price) * qty

                    balance_end = fetch_balance()

                    # write to DynamoDB
                    write_trade_close(
                        trade_id=   orders['trade_id'],
                        symbol=symbol,
                        close_price=exit_price,
                        pnl=         realised_pnl,
                        balance_end= balance_end
                    )
                    logging.info(f"{symbol}: trade closed @ {exit_price:.4f}, PnL={realised_pnl:.4f} USDT")

                    # reset
                    in_pos = False
                    orders = {}
                else:
                    time.sleep(10)
                    continue
            except Exception as e:
                logging.error(f"{symbol}: error checking position status → {e}")
                time.sleep(10)
                continue

        # ───────── only one entry per symbol per KillZone session ─────────
        if session != 'KillZone' or trade_taken_session:
            time.sleep(30)
            continue

        # ───────── log current mark price ─────────
        try:
            price = fetch_price(symbol)
            logging.info(f"{symbol}: current price = {price}")
        except Exception as e:
            logging.warning(f"{symbol}: failed fetch price → {e}")

        # ───────── load today’s candles and slice sessions ─────────Oka
        raw = fetch_ohlcv(symbol, TIMEFRAME, limit=500)
        df  = pd.DataFrame(raw, columns=['ts','open','high','low','close','volume'])
        df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
        df.set_index('ts', inplace=True)

        today    = df[df.index.date == now.date()]
        asia     = today[today.index.map(lambda t: get_session(t) == 'Asia')]
        london   = today[today.index.map(lambda t: get_session(t) == 'London')]
        killzone = today[today.index.map(lambda t: get_session(t) == 'KillZone')]

        # ───────── build and size the trade ─────────
        trade = build_trade(killzone, detect_sweep(asia, london), avail, asia, london)
        if not trade:
            time.sleep(30)
            continue
        # ───────── calculate & bump qty in one Decimal pass ─────────
        entry_price = Decimal(str(trade['entry']))
        raw_qty = Decimal(str(trade['qty']))
        # minimums as Decimal
        min_qty_dec = Decimal(str(min_qty))
        min_notional_q = (Decimal(str(min_cost)) / entry_price)

        # pick the largest: guarantees both min_qty & min_notional
        required_qty = max(raw_qty, min_qty_dec, min_notional_q)
        # quantize UP to the allowed precision
        qty_dec = required_qty.quantize(Decimal(f'1e-{q_prec}'), rounding=ROUND_UP)
        qty = float(qty_dec)
        notional = qty * float(entry_price)

        logging.info(f"{symbol}: bumped qty→{qty:.{q_prec}f} notional≈{notional:.2f}")
        side  = 'sell' if trade['dir'] == 'short' else 'buy'
        hedge = 'buy'  if side == 'sell' else 'sell'

        logging.info(f"{symbol}: placing {side} {qty} (notional≈{notional:.2f})")
        try:
            entry = place_entry(symbol, side, qty)
        except Exception as e:
            logging.error(f"{symbol}: entry order failed → {e}")
            time.sleep(60)
            continue

        # mark that we’ve now taken our one trade for this session
        trade_taken_session = True

        # capture details for later close
        entry_price = float(entry['price'])
        orders = {
            'trade_id':    write_trade_open(
                                symbol=symbol,
                                reason=f"{trade['dir']} sweep",
                                entry_price=entry_price,
                                tp=trade['tp'],
                                sl=trade['sl'],
                                balance_start=avail
                            ),
            'entry_price': entry_price,
            'qty':          qty,
            'dir':          trade['dir']
        }

        # write open to DynamoDB
        trade_id = write_trade_open(
            symbol=symbol,
            reason=f"{trade['dir']} sweep",
            entry_price=entry['price'],
            tp=trade['tp'],
            sl=trade['sl'],
            balance_start=avail,
        )
        orders['trade_id'] = trade_id

        # attach TP/SL
        try:
            tp_id, sl_id = attach_tp_sl(symbol, hedge, qty, trade['tp'], trade['sl'])
            orders.update({'tp': tp_id, 'sl': sl_id})
            in_pos = True
            logging.info(f"{symbol}: entry={entry['price']} TP={trade['tp']} SL={trade['sl']}")
        except Exception as e:
            logging.error(f"{symbol}: TP/SL attach failed → {e}")

        time.sleep(10)
