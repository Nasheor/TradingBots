import ccxt
import pandas as pd
import datetime
import time

# ----------------------------
# 0. GLOBAL SETTINGS
# ----------------------------
ACCOUNT_SIZE_START = 2000.0
RISK_PER_TRADE = 0.02
LEVERAGE = 25
RR_STATIC = 3.0
START_DATE = '2024-01-01T00:00:00Z'
TIMEFRAME = '5m'

# ----------------------------
# 1. FETCH FULL HISTORICAL DATA
# ----------------------------
def fetch_ohlcv(symbol='BTC/USDT', timeframe='5m', since=None):
    binance = ccxt.binance({
        'rateLimit': 1200,
        'enableRateLimit': True,
        # optional, but keeps things explicit
        'options': {'defaultType': 'future'}
    })

    since = since or binance.parse8601(START_DATE)
    all_candles = []

    print("Fetching historical data...")
    while True:
        candles = binance.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not candles:
            break
        all_candles += candles
        since = candles[-1][0] + 1
        time.sleep(1.1)
        if since >= int(datetime.datetime.now().timestamp() * 1000):
            break

    df = pd.DataFrame(all_candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df.set_index('timestamp', inplace=True)
    return df

# ----------------------------
# 2. SESSION UTILS
# ----------------------------
def get_session_label(ts):
    hour = ts.hour
    if 0 <= hour < 5:
        return 'Asia'
    elif 5 <= hour < 11:
        return 'London'
    elif 14 <= hour < 17:
        return 'KillZone'
    else:
        return 'Off'

def get_daily_sessions(df_5m):
    df_5m = df_5m.copy()
    df_5m['date'] = df_5m.index.date
    df_5m['session'] = df_5m.index.map(get_session_label)

    grouped = {}
    for dateval, day_df in df_5m.groupby('date'):
        sessions = {}
        for session, sess_df in day_df.groupby('session'):
            sessions[session] = sess_df
        grouped[dateval] = sessions
    return grouped

# ----------------------------
# 3. STRATEGY MODULES
# ----------------------------
def detect_sweeps_for_the_day(asian_df, london_df):
    if asian_df.empty or london_df.empty:
        return None

    asia_high = asian_df['high'].max()
    asia_low = asian_df['low'].min()
    london_high = london_df['high'].max()
    london_low = london_df['low'].min()

    high_swept = london_high > asia_high
    low_swept = london_low < asia_low

    if high_swept and not low_swept:
        return 'high'
    elif low_swept and not high_swept:
        return 'low'
    elif high_swept and low_swept:
        return 'both'
    else:
        return None

def execute_killzone_trade(killzone_df, sweep_side, account_balance):
    if killzone_df.empty or sweep_side is None or sweep_side == 'both':
        return None

    first_candle = killzone_df.iloc[0]
    entry_time = first_candle.name
    entry_price = first_candle['close']

    direction = 'short' if sweep_side == 'high' else 'long'
    sl = first_candle['low'] * 0.999 if direction == 'long' else first_candle['high'] * 1.001
    distance = abs(entry_price - sl)
    if distance <= 0:
        return None

    tp = entry_price + distance * RR_STATIC if direction == 'long' else entry_price - distance * RR_STATIC
    risk_amount = account_balance * RISK_PER_TRADE
    position_size = risk_amount / distance

    return {
        'entry_time': entry_time,
        'direction': direction,
        'entry': entry_price,
        'sl': sl,
        'tp': tp,
        'distance': distance,
        'position_size': position_size
    }

def evaluate_trade(kz_df, trade):
    eval_df = kz_df.loc[kz_df.index >= trade['entry_time']].copy()
    if eval_df.empty:
        return None

    entry = trade['entry']
    direction = trade['direction']
    sl = trade['sl']
    tp = trade['tp']
    pos_size = trade['position_size']
    result = None

    for ts, row in eval_df.iterrows():
        h, l = row['high'], row['low']
        if direction == 'long':
            if l <= sl:
                result = 'loss'
                exit_price = sl
                break
            if h >= tp:
                result = 'win'
                exit_price = tp
                break
        else:
            if h >= sl:
                result = 'loss'
                exit_price = sl
                break
            if l <= tp:
                result = 'win'
                exit_price = tp
                break

    if result is None:
        result = 'session_close'
        exit_price = eval_df.iloc[-1]['close']

    pnl = (exit_price - entry) * pos_size if direction == 'long' else (entry - exit_price) * pos_size

    return {
        'date': trade['entry_time'].date(),
        'entry_time': trade['entry_time'],
        'direction': direction,
        'entry': entry,
        'sl': sl,
        'tp': tp,
        'exit_price': exit_price,
        'result': result,
        'position_size': pos_size,
        'pnl_usd': pnl
    }

# ----------------------------
# 4. BACKTEST LOOP
# ----------------------------
def run_backtest(df_5m):
    account_balance = ACCOUNT_SIZE_START
    results = []

    daily_sessions = get_daily_sessions(df_5m)
    for dateval, sessions in sorted(daily_sessions.items()):
        asia_df = sessions.get('Asia', pd.DataFrame())
        london_df = sessions.get('London', pd.DataFrame())
        killzone_df = sessions.get('KillZone', pd.DataFrame())

        sweep = detect_sweeps_for_the_day(asia_df, london_df)
        trade = execute_killzone_trade(killzone_df, sweep, account_balance)

        if trade:
            result = evaluate_trade(killzone_df, trade)
            if result:
                account_balance += result['pnl_usd']
                result['account_balance'] = account_balance
                results.append(result)

    return pd.DataFrame(results)

# ----------------------------
# 5. MAIN EXECUTION
# ----------------------------
if __name__ == '__main__':
    symbols = ['SOL/USDT', 'BNB/USDT', 'XRP/USDT', 'LINK/USDT', 'BTC/USDT', 'ETH/USDT', 'LTC/USDT']

    for symbol in symbols:
        print(f"\n--- Backtesting {symbol} from Jan 1, 2025 ---")
        df = fetch_ohlcv(symbol, '5m')
        results = run_backtest(df)

        if not results.empty:
            csv_file = f"{symbol.replace('/', '')}_liquidity_backtest.csv"
            results.to_csv(csv_file, index=False)
            print(f"Results saved to {csv_file}")
            print(f"Final Balance for {symbol}: ${results.iloc[-1]['account_balance']:.2f}")
        else:
            print("No valid trades found.")
