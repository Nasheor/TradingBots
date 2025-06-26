# liquidity_bot/strategy.py

from decimal import Decimal, ROUND_DOWN, InvalidOperation
from config import RISK_PER_TRADE, LEVERAGE, RR_STATIC
import pandas as pd

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

    # 1) Entry is the first kill‐zone close
    c0    = k_df.iloc[0]
    entry = c0['close']

    # 2) TP at the full session extreme
    if sweep == 'low':      # bias long
        extreme = max(asia_df['high'].max(), london_df['high'].max())
        direction = 'long'
    else:                   # bias short
        extreme = min(asia_df['low'].min(),  london_df['low'].min())
        direction = 'short'
    tp = extreme

    # 3) full reward‐distance = |TP – ENTRY|
    total_dist = abs(tp - entry)

    # 4) 1/3 of that is your SL‐distance
    risk_dist = total_dist / RR_STATIC

    # 5) SL on the opposite side of entry
    if direction == 'long':
        sl = entry - risk_dist
    else:
        sl = entry + risk_dist

    # 6) position sizing: risk‐per‐trade vs. margin‐limit
    risk_usd   = avail * RISK_PER_TRADE
    qty_risk   = risk_usd / abs(entry - sl)
    qty_margin = risk_usd * LEVERAGE / entry
    qty        = min(qty_risk, qty_margin)

    # 7) return everything
    return {
        'entry':   entry,
        'tp':      tp,
        'sl':      sl,
        'dir':     direction,
        'qty':     qty,
        'tp_diff': abs(tp    - entry),
        'sl_diff': abs(entry - sl),
    }


# ——————————————————————————————————————————————
# quick sanity check (drop into your main or a REPL)
if __name__ == '__main__':
    # pretend killzone/asia/london extremes:
    asia = pd.DataFrame({'high':[150], 'low':[145]})
    london = pd.DataFrame({'high':[155], 'low':[148]})
    kill = pd.DataFrame({'close':[151]})
    trade = build_trade(kill, 'low', avail=100, asia_df=asia, london_df=london)
    print(trade)
    # should show:
    # entry=151, tp=155 → total_dist=4
    # sl = 151 - 4/3 ≃ 149.6667  → sl_diff ≃ 1.3333
    # qty ≃ min( (2$/1.3333)=1.5, (2$*25/151)=0.331...) = 0.331...
