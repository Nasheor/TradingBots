# ─── File: exchange.py ───

import ccxt
import os

# base = 'https://fapi.binance.com'

EXCHANGE = ccxt.binance({
    'apiKey': os.getenv("BINANCE_API_KEY"),
    'secret': os.getenv("BINANCE_API_SECRET"),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'
    }
    # 'urls': {'api': {'public': base, 'private': base}},
})
