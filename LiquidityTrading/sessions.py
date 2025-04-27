# liquidity_bot/sessions.py
import datetime as dt

def get_session(ts: dt.datetime):
    h = ts.hour
    if h < 5:   return 'Asia'
    if h < 11:  return 'London'
    if 14 <= h < 23: return 'KillZone'
    return 'Off'

def detect_sweep(asia_df, london_df):
    if asia_df.empty or london_df.empty:
        return None
    a_hi, a_lo = asia_df['high'].max(), asia_df['low'].min()
    l_hi, l_lo = london_df['high'].max(), london_df['low'].min()
    if l_hi > a_hi and l_lo < a_lo:
        return 'both'
    if l_hi > a_hi:
        return 'high'
    if l_lo < a_lo:
        return 'low'
    return None
