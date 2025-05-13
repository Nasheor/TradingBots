"""
Core idea:
  1. During the 5 m “KillZone” (14:00–17:00 UTC), look for a “sweep” bias:
     did London price action sweep above Asia’s high → short bias
     or below Asia’s low → long bias?
  2. Check the 2 h chart is trending in the same direction (via a 150-EMA check).
  3. Wait for the first market-structure shift on the 5 m that agrees with that bias.
  4. Size the position so that you risk exactly 2 % of your free USDT,
     honoring Binance’s minimum size & notional requirements.
  5. Place a MARKET entry + attached TP/SL orders, track it in DynamoDB,
     and never take more than one trade per symbol per KillZone session.

This worker() function is meant to be run in a dedicated thread per symbol.
"""
import time
import datetime as dt
import logging
import pandas as pd
import pandas_ta as ta
from decimal      import Decimal, ROUND_UP
from config       import TIMEFRAME, LEVERAGE
from exchange     import EX, fetch_balance, fetch_price, fetch_ohlcv
from sessions     import get_session, detect_sweep
from strategy     import build_trade, d_round
from orders       import set_leverage, place_entry, attach_tp_sl
from dynamo       import write_trade_open, write_trade_close
from structure    import detect_structure, ema_trend_signal  # now returns 'bullish', 'bearish', or None

"""
Main loop for a single symbol:
 - sets leverage
 - polls existing positions
 - detects sweep bias (Asia→London)
 - enforces 2 h trend alignment (150 EMA)
 - waits for 5 m structure shift matching bias
 - sizes & places trade (2 % risk)
 - attaches TP/SL, logs PnL on close
"""
def worker(symbol):
    # ───────── initialize symbol thread ─────────
    # 1) Set our desired leverage on Binance Futures
    set_leverage(symbol, LEVERAGE)

    # 2) Fetch initial free USDT balance
    avail = fetch_balance()
    logging.info(f"{symbol}: available balance = {avail:.2f} USDT")

    # ───────── load Binance filters ─────────
    # 3) Load market filters (precision, minimums) from CCXT’s cached market info
    mkt      = EX.market(symbol)
    p_prec   = int(mkt['precision'].get('price', 2) or 2)
    q_prec   = int(mkt['precision'].get('amount', 3) or 3)
    min_qty  = float(mkt['limits']['amount']['min'] or 0.0)
    min_cost = float(mkt['limits']['cost']['min']   or 0.0)

    # State variables
    in_pos              = False
    orders              = {}
    taken_this_session  = False
    last_session        = None

    # ─────────────────────────────────────────────────────────────
    # 4) Main perpetual loop
    # ─────────────────────────────────────────────────────────────
    while True:
        # a) Get the current UTC time and map to our sessions
        now     = dt.datetime.now(dt.timezone.utc)
        session = get_session(now)

        # ───────── reset per-KillZone-session flags ─────────
        # b) If session changed, clear our “one‐trade” flag
        if session != last_session:
            taken_this_session = False
            last_session       = session

        # ───────── monitor existing position for close ─────────
        if in_pos:
            try:
                statuses = []
                # gather statuses of our TP & SL orders
                for side in ('tp','sl'):
                    oid = orders.get(side)
                    if isinstance(oid, dict):  # sometimes attach returns order‐dict
                        oid = oid.get('id')
                    if not oid:
                        continue
                    statuses.append(EX.fetch_order(oid, symbol)['status'])

                # if either target or stop hits, we’re flat
                if any(s in ('closed','canceled') for s in statuses):
                    exit_p     = float(fetch_price(symbol))
                    entry_p    = orders['entry_price']
                    qty        = orders['qty']
                    dirn       = orders['dir']

                    # compute PnL depending on direction
                    pnl        = ((exit_p - entry_p) if dirn=='long'
                                  else (entry_p - exit_p)) * qty
                    bal_end    = fetch_balance()

                    # record close in DynamoDB
                    write_trade_close(
                        trade_id=orders['trade_id'],
                        symbol=symbol,
                        close_price=exit_p,
                        pnl=pnl,
                        balance_end=bal_end,
                    )
                    logging.info(f"{symbol}: closed @ {exit_p:.4f}, PnL={pnl:.4f}")

                    # reset state
                    in_pos, orders = False, {}
                else:
                    # still waiting for TP/SL, pause briefly
                    time.sleep(10)
                    continue
            except Exception as e:
                logging.error(f"{symbol}: error polling position → {e}")
                time.sleep(10)
                continue

        # ───────── skip unless fresh KillZone session entry allowed ─────────
        if session != 'KillZone' or taken_this_session:
            time.sleep(30)
            continue

        # ───────── refresh balance & price ─────────
        avail = fetch_balance()
        logging.info(f"{symbol}: balance = {avail:.2f} USDT")
        try:
            price = fetch_price(symbol)
            logging.info(f"{symbol}: market price = {price:.4f}")
        except Exception as e:
            logging.warning(f"{symbol}: price fetch failed → {e}")

        # ───────── build session‐sliced 5 m candles ─────────
        o5m = fetch_ohlcv(symbol, TIMEFRAME, limit=500)
        df5 = pd.DataFrame(o5m, columns=['ts','open','high','low','close','vol'])
        df5['ts']      = pd.to_datetime(df5['ts'], unit='ms', utc=True)
        df5.set_index('ts', inplace=True)
        today    = df5[df5.index.date == now.date()]
        asia     = today[today.index.map(lambda t: get_session(t)=='Asia')]
        london   = today[today.index.map(lambda t: get_session(t)=='London')]
        killzone = today[today.index.map(lambda t: get_session(t)=='KillZone')]

        # ───────── detect the Asia⇢London “sweep” bias ─────────
        bias = detect_sweep(asia, london)  # 'high' or 'low' or None
        if not bias:
            time.sleep(30)
            continue

        # ───────── 2 h trend via 50-EMA backtest logic ─────────
        o2h = EX.fetch_ohlcv(symbol, '2h', limit=100)
        df2h = pd.DataFrame(o2h, columns=['ts','open','high','low','close','vol'])
        df2h['ts'] = pd.to_datetime(df2h['ts'], unit='ms', utc=True)
        df2h.set_index('ts', inplace=True)
        # use our shared function: length=50, backcandles=8
        sig = ema_trend_signal(df2h, length=50, backcandles=8)
        last_sig = sig.iat[-1]

        # require signal==2 for long, ==1 for short
        if bias=='low'  and last_sig!=2:
            logging.info(f"{symbol}: long bias but 2h EMASignal={last_sig}, skip")
            time.sleep(30)
            continue
        if bias=='high' and last_sig!=1:
            logging.info(f"{symbol}: short bias but 2h EMASignal={last_sig}, skip")
            time.sleep(30)
            continue

        # ───────── preliminary trade params from killzone candles ─────────
        trade = build_trade(killzone, bias, avail, asia, london)
        if not trade:
            time.sleep(30)
            continue

        # ───────── wait for a structure shift matching bias ─────────
        kz = killzone.copy().reset_index(drop=False)
        while True:
            idx    = len(kz)-1
            struct = detect_structure(kz, idx, backcandles=30, pivot_window=5)
            # only take bullish shifts for long, bearish for short
            if bias=='low'  and struct=='bullish':
                trade['entry'] = float(kz.iloc[idx]['close'])
                logging.info(f"{symbol}: bullish shift at {kz.iloc[idx]['ts']}, entry={trade['entry']:.4f}")
                break
            if bias=='high' and struct=='bearish':
                trade['entry'] = float(kz.iloc[idx]['close'])
                logging.info(f"{symbol}: bearish shift at {kz.iloc[idx]['ts']}, entry={trade['entry']:.4f}")
                break
            time.sleep(5)
            # reload killzone data
            raw   = fetch_ohlcv(symbol, TIMEFRAME, limit=500)
            kz    = pd.DataFrame(raw, columns=['ts','open','high','low','close','volume'])
            kz['ts'] = pd.to_datetime(kz['ts'], unit='ms', utc=True)
            kz    = kz[kz['ts'].dt.date==now.date()].reset_index(drop=True)

        # ───────── final sizing, respect 2 % risk & Binance minima ─────────
        ep    = Decimal(str(trade['entry']))
        rq    = Decimal(str(trade['qty']))
        m_q   = Decimal(str(min_qty))
        m_n   = Decimal(str(min_cost)) / ep
        size  = max(rq, m_q, m_n).quantize(Decimal(f'1e-{q_prec}'), rounding=ROUND_UP)
        qty   = float(size)
        noti  = qty * float(ep)
        logging.info(f"{symbol}: final qty→{qty:.{q_prec}f} notional≈{noti:.2f}")

        side  = 'sell' if bias=='high' else 'buy'
        hedge = 'buy'  if side=='sell' else 'sell'

        # ───────── place market entry + attach TP/SL ─────────
        try:
            entry = place_entry(symbol, side, qty)
        except Exception as e:
            logging.error(f"{symbol}: entry failed → {e}")
            time.sleep(60)
            continue

        taken_this_session = True
        ep_float = float(entry['price'])
        tid = write_trade_open(
            symbol=symbol,
            reason=f"{bias} sweep + 5m structure",
            entry_price=entry['price'],
            tp=trade['tp'],
            sl=trade['sl'],
            balance_start=avail
        )
        orders = {'trade_id':tid, 'entry_price':ep_float, 'qty':qty, 'dir':trade['dir']}

        try:
            tp_id, sl_id = attach_tp_sl(symbol, hedge, qty, trade['tp'], trade['sl'])
            orders.update(tp=tp_id, sl=sl_id)
            in_pos = True
            logging.info(f"{symbol}: entry={ep_float:.4f} TP={trade['tp']:.4f} SL={trade['sl']:.4f}")
        except Exception as e:
            logging.error(f"{symbol}: TP/SL attach error → {e}")

        time.sleep(10)
