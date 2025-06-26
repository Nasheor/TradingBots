# liquidity_bot/config.py
import logging
import socket

RISK_PER_TRADE = 0.02       # 2% risk per trade
LEVERAGE       = 30
RR_STATIC      = 3.0
# Up‑/Down‑trend detection (2‑hour candles)
TIMEFRAME_TREND = "2h"
# Execution timeframe (entries placed on the close of a 15‑minute candle)
TIMEFRAME_TRADE = "15m"
# SYMBOLS        = ['SOL/USDT', 'LTC/USDT', 'LINK/USDT', 'XRP/USDT']SYMBOLS           44
SYMBOLS        = ['SOL/USDT']
TESTNET        = False
API_KEY        = ""  # pull from env in real code
API_SECRET     = ""
AWS_ACCESS_KEY = ""
AWS_SECRET_KEY = ""
REGION_NAME    = "eu-west-1"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        # logging.FileHandler("/home/ec2-user/TradingBots/LiquidityTrading/live_bot.log"),
        logging.FileHandler("live_bot.log"),
        logging.StreamHandler()
    ]
)
logging.info(f"Trend bot running on {socket.gethostname()}")
