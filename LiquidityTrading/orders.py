# liquidity_bot/orders.py
import logging
import ccxt
from exchange import EX

def set_leverage(symbol, leverage):
    try:
        EX.set_leverage(leverage, symbol)
    except Exception as e:
        logging.warning(f"{symbol}: leverage set failed {e}")

def place_entry(symbol, side, qty):
    return EX.create_order(symbol, 'MARKET', side.upper(), qty)

def attach_tp_sl(symbol, hedge, qty, tp, sl):
    tp_id = EX.create_order(symbol, 'LIMIT', hedge.upper(), qty, tp,
                            {'reduceOnly': True, 'timeInForce':'GTC'})['id']
    sl_id = EX.create_order(symbol, 'STOP_MARKET', hedge.upper(), qty, None,
                            {'stopPrice': sl, 'reduceOnly': True})['id']
    return tp_id, sl_id
