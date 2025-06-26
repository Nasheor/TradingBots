from __future__ import annotations
import time, datetime as dt, logging
from decimal import Decimal, ROUND_UP
from typing import Dict, Optional
import pandas as pd

from exchange   import EX, fetch_balance, fetch_price, fetch_ohlcv
from orders     import set_leverage, place_entry, attach_tp_sl
from strategy   import TrendStrategy
from config     import (LEVERAGE, RISK_PER_TRADE, TIMEFRAME_TRADE, TIMEFRAME_TREND)
from dynamo    import write_trade_open, write_trade_close

# Rounding helpers
ROUND_PRICE = lambda p, prec: float(Decimal(str(p)).quantize(Decimal(f'1e-{prec}'), ROUND_UP))
ROUND_QTY   = ROUND_PRICE


def trend_worker(symbol: str):
    set_leverage(symbol, LEVERAGE)
    balance = fetch_balance()
    logging.info(f"{symbol}: available balance ≈ {balance:.2f} USDT – trend worker starting")

    market   = EX.market(symbol)
    p_prec   = int(market['precision'].get('price', 2) or 2)
    q_prec   = int(market['precision'].get('amount', 3) or 3)
    min_qty  = float(market['limits']['amount']['min'] or 0)
    min_cost = float(market['limits']['cost']['min'] or 0)

    strategy = TrendStrategy(client=EX, symbol=symbol,
                             trend_tf=TIMEFRAME_TREND, trade_tf=TIMEFRAME_TRADE)

    in_pos   = False
    orders: Dict[str, Optional[dict | str]] = {}

    while True:
        try:
            if in_pos:
                closed, pnl = _poll_exit(symbol, orders)
                if closed:
                    # write close to DynamoDB
                    write_trade_close(
                        orders['trade_id'], symbol,
                        orders['exit_price'], pnl, fetch_balance()
                    )
                    in_pos, orders = False, {}
                time.sleep(5)
                continue

            entry = strategy.on_new_trade_candle()
            if entry is None:
                time.sleep(5)
                continue

            # size calculation
            balance = fetch_balance()
            sl_diff   = abs(entry['price'] - entry['sl'])
            risk_usd  = balance * RISK_PER_TRADE
            qty_risk  = risk_usd / sl_diff
            qty_margin= risk_usd * LEVERAGE / entry['price']
            raw_qty   = min(qty_risk, qty_margin)

            ep_dec    = Decimal(str(entry['price']))
            required_q= max(
                Decimal(str(raw_qty)), Decimal(str(min_qty)),
                Decimal(str(min_cost)) / ep_dec
            )
            qty       = float(required_q.quantize(Decimal(f'1e-{q_prec}'), ROUND_UP))

            logging.info(f"{symbol}: {entry['side']} {qty:.{q_prec}f} @ {entry['price']:.{p_prec}f}")

            # place entry
            order_side = 'sell' if entry['side']=='SELL' else 'buy'
            hedge_side = 'buy'  if entry['side']=='SELL' else 'sell'
            entry_order = place_entry(symbol, order_side, qty)

            # write open to DynamoDB
            trade_id = write_trade_open(
                symbol,
                f"Trend {entry['side']}",
                entry_order['price'], entry['tp'], entry['sl'], balance
            )

            # attach TP/SL
            tp_id, sl_id = attach_tp_sl(
                symbol, hedge_side, qty, entry['tp'], entry['sl']
            )

            orders = {
                'trade_id':   trade_id,
                'entry_price': float(entry_order['price']),
                'qty':         qty,
                'dir':         'short' if entry['side']=='SELL' else 'long',
                'tp':          entry['tp'],
                'sl':          entry['sl'],
                'tp_id':       tp_id,
                'sl_id':       sl_id,
                'exit_price':  None
            }
            in_pos = True
            logging.info(
                f"{symbol}: in-position @ {orders['entry_price']:.{p_prec}f} → TP {orders['tp']:.{p_prec}f} / SL {orders['sl']:.{p_prec}f}"
            )

        except Exception as e:
            logging.exception(f"{symbol}: worker exception – {e}")
            time.sleep(10)


def _poll_exit(symbol: str, orders: Dict[str, any]) -> tuple[bool, float]:
    try:
        for side in ('tp', 'sl'):
            oid = orders.get(f"{side}_id")
            if not oid:
                continue
            status = EX.fetch_order(oid, symbol)['status']
            if status in ('closed', 'canceled'):
                exit_p = float(fetch_price(symbol))
                dirn   = orders['dir']
                entry_p= orders['entry_price']
                qty    = orders['qty']
                pnl    = ((exit_p-entry_p) if dirn=='long' else (entry_p-exit_p)) * qty
                orders['exit_price'] = exit_p
                logging.info(f"{symbol}: closed @ {exit_p:.4f}, PnL={pnl:.2f} USDT")
                return True, pnl
    except Exception as e:
        logging.error(f"{symbol}: exit poll error – {e}")
    return False, 0.0