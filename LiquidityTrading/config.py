# liquidity_bot/config.py
import logging
import socket

RISK_PER_TRADE = 0.02       # 2% risk per trade
LEVERAGE       = 30
RR_STATIC      = 3.0
TIMEFRAME      = '5m'
SYMBOLS        = ['SOL/USDT','XRP/USDT','LINK/USDT','LTC/USDT']
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
        logging.FileHandler("/home/ec2-user/TradingBots/LiquidityTrading/live_bot.log"),
        # logging.FileHandler("live_bot.log"),
        logging.StreamHandler()
    ]
)
logging.info(f"Running on {socket.gethostname()}")
