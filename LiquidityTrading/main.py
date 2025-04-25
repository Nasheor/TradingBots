# liquidity_bot/main.py
import time, logging, threading
from config import SYMBOLS
from worker import worker

if __name__ == '__main__':
    logging.info("Bot starting – one trade/day; 2% risk; logging price")
    for sym in SYMBOLS:
        t = threading.Thread(target=worker, args=(sym,), daemon=True)
        t.start()
    while True:
        time.sleep(60)
