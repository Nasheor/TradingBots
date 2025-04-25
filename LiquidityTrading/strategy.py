# liquidity_bot/strategy.py
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from .config import RISK_PER_TRADE, LEVERAGE, RR_STATIC

def d_round(val, prec):
    try:
        return float(Decimal(val).quantize(Decimal(f'1e-{prec}'), ROUND_DOWN))
    except InvalidOperation:
        return val

def build_trade(k_df, sweep, avail, asia_df, london_df):
    """
    k_df        → KillZone candles
    sweep       → 'high' or 'low'
    avail       → free balance
    asia_df     → Asia session candles
    london_df   → London session candles
    """
    if k_df.empty or sweep in (None, 'both'):
        return None

    # 1) Entry is first KillZone close
    c0    = k_df.iloc[0]
    entry = c0['close']

    # 2) Determine TP from session extremes
    if sweep == 'high':   # we go SHORT
        tp        = entry
        tp        = max(asia_df['low'].min(), london_df['low'].min()) - (max(asia_df['low'].min(), london_df['low'].min()) - min(asia_df['low'].min(), london_df['low'].min()))*0.5
        direction = 'short'
    else:                 # sweep == 'low' → we go LONG
        tp        = min(asia_df['high'].max(), london_df['high'].max()) + (max(asia_df['high'].max(), london_df['high'].max()) - min(asia_df['high'].max(), london_df['high'].max()))*0.5
        direction = 'long'

    # 3) Compute total reward-distance, then derive SL so R:R = 1:3
    total_dist  = abs(entry - tp)
    risk_dist   = total_dist / RR_STATIC
    # For a short, SL is above entry; for a long, SL is below:
    if direction == 'short':
        sl = entry + risk_dist
    else:
        sl = entry - risk_dist

    # 4) Now size the position so margin and SL-risk stay ≤ RISK_PER_TRADE
    risk_usd    = avail * RISK_PER_TRADE           # cash you’re willing to lose
    qty_risk    = risk_usd / abs(entry - sl)       # so SL-hit costs ≤ risk_usd
    qty_margin  = risk_usd * LEVERAGE / entry      # margin cost ≤ risk_usd
    qty         = min(qty_risk, qty_margin)

    return {
        'entry':    entry,
        'sl':       sl,
        'tp':       tp,
        'dir':      direction,
        'qty':      qty,
        'sl_diff':  abs(entry - sl),
        'tp_diff':  abs(entry - tp),
    }
