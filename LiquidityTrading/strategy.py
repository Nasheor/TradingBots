# liquidity_bot/strategy.py
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from config import RISK_PER_TRADE, LEVERAGE, RR_STATIC

def d_round(val, prec):
    try:
        return float(Decimal(val).quantize(Decimal(f'1e-{prec}'), ROUND_DOWN))
    except InvalidOperation:
        return val

def build_trade(k_df, sweep, avail):
    if k_df.empty or sweep in (None, 'both'):
        return None
    c0 = k_df.iloc[0]
    entry = c0['close']
    direction = 'short' if sweep=='high' else 'long'
    sl = c0['low']*0.999 if direction=='long' else c0['high']*1.001
    sl_diff = abs(entry - sl)
    tp = entry + sl_diff * RR_STATIC if direction=='long' else entry - sl_diff * RR_STATIC

    risk       = avail * RISK_PER_TRADE
    qty_risk   = risk / sl_diff
    qty_margin = risk * LEVERAGE / entry
    qty        = min(qty_risk, qty_margin)

    return {
        'entry':    entry,
        'sl':       sl,
        'tp':       tp,
        'dir':      direction,
        'qty':      qty,
        'sl_diff':  sl_diff
    }
