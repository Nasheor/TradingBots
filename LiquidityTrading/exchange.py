# liquidity_bot/exchange.py
import ccxt
from config import TESTNET, API_KEY, API_SECRET

def make_exchange():
    base = 'https://testnet.binancefuture.com' if TESTNET else 'https://fapi.binance.com'
    ex = ccxt.binanceusdm({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',
            'adjustForTimeDifference': True,
            'recvWindow': 60000,
        },
        'urls': {'api': {'public': base, 'private': base}},
    })
    ex.load_markets()
    return ex

# Singleton
EX = make_exchange()

def fetch_balance():
    bal = EX.fetch_balance({'type':'future'})
    return float(bal['info']['availableBalance'])

def fetch_price(symbol):
    return EX.fetch_ticker(symbol)['last']

def fetch_ohlcv(symbol, timeframe, limit=500):
    return EX.fetch_ohlcv(symbol, timeframe, limit=limit)

if __name__ == '__main__':
    print(EX.fetch_balance({'type': 'future'}))
