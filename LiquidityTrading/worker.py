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
from structure    import detect_structure, ema_trend_signal


def worker(symbol):
    # ───────── initialize symbol thread ─────────
    set_leverage(symbol, LEVERAGE)
    avail = fetch_balance()
    logging.info(f"{symbol}: available balance = {avail:.2f} USDT")

    # ───────── load Binance filters ─────────
    market   = EX.market(symbol)
    p_prec   = int(market['precision'].get('price', 2) or 2)
    q_prec   = int(market['precision'].get('amount', 3) or 3)
    min_qty  = float(market['limits']['amount']['min'] or 0.0)
    min_cost = float(market['limits']['cost']['min']   or 0.0)

    in_pos               = False
    orders               = {}
    trade_taken_session  = False
    last_session         = None

    while True:
        now     = dt.datetime.now(dt.timezone.utc)
        session = get_session(now)

        # ───────── reset per-KillZone-session flags ─────────
        if session != last_session:
            trade_taken_session = False
            last_session        = session

        # ───────── check for close of existing position ─────────
        if in_pos:
            try:
                statuses = []
                for side in ('tp','sl'):
                    oid = orders.get(side)
                    if isinstance(oid, dict):
                        oid = oid.get('id')
                    if not oid:
                        continue
                    statuses.append(EX.fetch_order(oid, symbol)['status'])
                if any(s in ('closed','canceled') for s in statuses):
                    exit_p     = float(fetch_price(symbol))
                    entry_p    = orders['entry_price']
                    qty        = orders['qty']
                    dirn       = orders['dir']
                    pnl        = ((exit_p-entry_p) if dirn=='long'
                                  else (entry_p-exit_p)) * qty
                    bal_end    = fetch_balance()
                    write_trade_close(
                        trade_id=   orders['trade_id'],
                        symbol=     symbol,
                        close_price=exit_p,
                        pnl=         pnl,
                        balance_end= bal_end
                    )
                    logging.info(f"{symbol}: closed @ {exit_p:.4f}, PnL={pnl:.4f}")
                    in_pos, orders = False, {}
                else:
                    time.sleep(10)
                    continue
            except Exception as e:
                logging.error(f"{symbol}: error polling position → {e}")
                time.sleep(10)
                continue

        # ───────── allow one entry per symbol per KillZone session ─────────
        if session != 'KillZone' or trade_taken_session:
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

        # ───────── load today’s 5 m candles ─────────
        raw5m = fetch_ohlcv(symbol, TIMEFRAME, limit=500)
        df5   = pd.DataFrame(raw5m, columns=['ts','open','high','low','close','vol'])
        df5['ts'] = pd.to_datetime(df5['ts'], unit='ms', utc=True)
        df5.set_index('ts', inplace=True)
        today = df5[df5.index.date == now.date()]
        if today.empty:
            time.sleep(30)
            continue

        # ───────── slice Asia / London / KillZone ─────────
        asia     = today[today.index.map(lambda t: get_session(t)=='Asia')]
        london   = today[today.index.map(lambda t: get_session(t)=='London')]
        killzone = today[today.index.map(lambda t: get_session(t)=='KillZone')]
        if asia.empty or london.empty or killzone.empty:
            time.sleep(30)
            continue

        # ───────── detect sweep bias ─────────
        bias = detect_sweep(asia, london)
        if not bias:
            time.sleep(30)
            continue

        # ───────── 2 h trend alignment via EMA signal ─────────
        raw2h = EX.fetch_ohlcv(symbol, '2h', limit=200)
        df2h  = pd.DataFrame(raw2h, columns=['ts','open','high','low','close','vol'])
        df2h['ts'] = pd.to_datetime(df2h['ts'], unit='ms', utc=True)
        df2h.set_index('ts', inplace=True)
        sig2h = ema_trend_signal(df2h, length=150, backcandles=15)
        last_sig = sig2h.iat[-1]
        if bias=='low'  and last_sig != 2:
            logging.info(f"{symbol}: long bias but 2h EMASignal={last_sig}, skip")
            time.sleep(30)
            continue
        if bias=='high' and last_sig != 1:
            logging.info(f"{symbol}: short bias but 2h EMASignal={last_sig}, skip")
            time.sleep(30)
            continue

        # ───────── preliminary TP/SL sizing ─────────
        tr = build_trade(killzone, bias, avail, asia, london)
        if not tr:
            time.sleep(30)
            continue

        # ───────── prepare full‐day DF for structure detection ─────────
        day_full   = today.reset_index(drop=False)
        timestamps = day_full['ts']

        # # ───────── find first 5 m structure shift at killzone timestamps ─────────
        # entry_price = None
        # for ts in killzone.index:
        #     pos = timestamps.searchsorted(ts)
        #     if detect_structure(day_full, pos, backcandles=30, pivot_window=5):
        #         entry_price = float(day_full.iloc[pos]['close'])
        #         logging.info(f"{symbol}: structure shift at {ts}, entry={entry_price:.4f}")
        #         break
        # ----------- ENTRY: first 200-EMA crossover inside KillZone---------------
        kz = killzone.copy()
        kz['EMA200'] = kz['close'].ewm(span=200, adjust=False).mean()
        entry_price = None
        for ts, row in kz.iterrows():
            price = float(row['close'])
            ema = float(row['EMA200'])
            if bias == 'low' and price >= ema:
                entry_price = price
                logging.info(f"{symbol}: EMA200 crossover at {ts}, entry={entry_price:.4f}")
                break
            if bias == 'high' and price <= ema:
                entry_price = price
                logging.info(f"{symbol}: EMA200 crossover at {ts}, entry={entry_price:.4f}")
                break

        # ───────── fallback to first killzone bar if no shift ─────────
        if entry_price is None:
            entry_price = float(killzone.iloc[0]['close'])
            logging.info(f"{symbol}: no structure shift → fallback entry={entry_price:.4f}")

        # ───────── final sizing: 2 % risk & Binance minima ─────────
        sl_diff    = abs(entry_price - tr['sl'])
        risk_usd   = avail * RISK_PER_TRADE
        qty_risk   = risk_usd / sl_diff
        qty_margin = risk_usd * LEVERAGE / entry_price
        raw_qty    = min(qty_risk, qty_margin)

        ep_dec      = Decimal(str(entry_price))
        required_q  = max(
            Decimal(str(raw_qty)),
            Decimal(str(min_qty)),
            Decimal(str(min_cost)) / ep_dec
        )
        qty_dec = required_q.quantize(Decimal(f'1e-{q_prec}'), ROUND_UP)
        qty     = float(qty_dec)
        notional = qty * entry_price

        logging.info(f"{symbol}: placing {'sell' if bias=='high' else 'buy'} {qty:.{q_prec}f} notional≈{notional:.2f}")
        try:
            entry = place_entry(symbol, bias=='high' and 'sell' or 'buy', qty)
        except Exception as e:
            logging.error(f"{symbol}: entry order failed → {e}")
            time.sleep(60)
            continue

        trade_taken_session = True

        # ───────── write open to DynamoDB ─────────
        ep_float = float(entry['price'])
        tid = write_trade_open(
            symbol=symbol,
            reason=f"{bias} sweep + 5m structure",
            entry_price=ep_float,
            tp=tr['tp'],
            sl=tr['sl'],
            balance_start=avail,
        )
        orders = {'trade_id': tid, 'entry_price': ep_float, 'qty': qty, 'dir': tr['dir']}

        # ───────── attach TP/SL ─────────
        hedge = bias=='high' and 'buy' or 'sell'
        try:
            tp_id, sl_id = attach_tp_sl(symbol, hedge, qty, tr['tp'], tr['sl'])
            orders.update({'tp': tp_id, 'sl': sl_id})
            in_pos = True
            logging.info(f"{symbol}: entry={ep_float:.4f} TP={tr['tp']:.4f} SL={tr['sl']:.4f}")
        except Exception as e:
            logging.error(f"{symbol}: TP/SL attach failed → {e}")

        time.sleep(10)

if __name__ == '__main__':
    logging.info("Bot starting – one trade per symbol per UTC day · 2 % risk with trend alignment")
    import threading
    for s in SYMBOLS:
        threading.Thread(target=worker, args=(s,), daemon=True).start()
    while True:
        time.sleep(60)
