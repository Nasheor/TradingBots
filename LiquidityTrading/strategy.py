# liquidity_bot/strategy.py

from decimal import Decimal, ROUND_DOWN, InvalidOperation
from config import RISK_PER_TRADE, LEVERAGE, RR_STATIC

def d_round(val, prec):
    try:
        return float(Decimal(val).quantize(Decimal(f'1e-{prec}'), ROUND_DOWN))
    except InvalidOperation:
        return val

def build_trade(k_df, sweep, avail, asia_df, london_df):
    """
    k_df      → KillZone candles (5 m)
    sweep     → 'high' or 'low'
    avail     → free balance
    asia_df   → Asia session candles
    london_df → London session candles
    """
    if k_df.empty or sweep in (None, 'both'):
        return None

    # 1) Entry is first KillZone close
    c0    = k_df.iloc[0]
    entry = c0['close']

    # 2) TP at the full-session extreme
    if sweep == 'low':    # we go LONG
        extreme_high = max(asia_df['high'].max(), london_df['high'].max())
        tp            = extreme_high
        direction     = 'long'
    else:                 # sweep == 'high' → we go SHORT
        extreme_low  = min(asia_df['low'].min(),  london_df['low'].min())
        tp            = extreme_low
        direction     = 'short'

    # 3) Compute full reward-distance, then SL so reward:risk = 3:1
    total_dist = abs(tp - entry)
    risk_dist  = total_dist / RR_STATIC   # one-third of the total
    if direction == 'long':
        sl = entry - risk_dist
    else:
        sl = entry + risk_dist

    # 4) Position sizing: 2 % risk or ≤ 2 % margin, whichever is smaller
    risk_usd   = avail * RISK_PER_TRADE              # max $ to lose
    qty_risk   = risk_usd / abs(entry - sl)          # so SL-hit costs ≤ risk_usd
    qty_margin = risk_usd * LEVERAGE / entry         # so required initial margin ≤ risk_usd
    qty        = min(qty_risk, qty_margin)

    return {
        'entry':   entry,
        'sl':      sl,
        'tp':      tp,
        'dir':     direction,
        'qty':     qty,
        'sl_diff': abs(entry - sl),
        'tp_diff': abs(tp    - entry),
    }
