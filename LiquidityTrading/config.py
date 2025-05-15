# liquidity_bot/config.py
import logging
import socket

RISK_PER_TRADE = 0.02       # 2% risk per trade
LEVERAGE       = 30
RR_STATIC      = 3.0
TIMEFRAME      = '5m'
# SYMBOLS        = ['SOL/USDT', 'LTC/USDT', 'LINK/USDT', 'XRP/USDT']
SYMBOLS        = ['SOL/USDT']
TESTNET        = False
API_KEY        = "rdpvKsuXdhdNXHPAM7XgZ6sfCQXLXBvfNFLMEQZNqaeHilqbIREar8LXWj65x8z8"  # pull from env in real code
API_SECRET     = "jciGO3TOYa5CSHVS1qWG2H0gV7hCtiRyC8eM3x5x3AqiRN2iXg91Z3uapXDsieLx"
AWS_ACCESS_KEY = "AKIAVGIWSCAHROXEOSUZ"
AWS_SECRET_KEY = "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"
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
