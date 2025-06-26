# main_trend.py
"""
Orchestrator launching one *trend_worker* thread per symbol, identical to
*liquidity_bot/main.py* so it can be supervised by the same process manager or
`systemd` service.
"""
import time
import logging
import threading

from config import SYMBOLS
from worker  import trend_worker


def main():
    logging.info("Trend bot starting – continuous trend‑following across symbols")
    for sym in SYMBOLS:
        t = threading.Thread(target=trend_worker, args=(sym,), daemon=True)
        t.start()
    # Keep the main thread alive forever so Docker / supervisor sees the ...

if __name__ == '__main__':
    main()