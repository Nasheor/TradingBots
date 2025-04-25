# liquidity_bot/dynamo.py
import uuid, datetime as dt, logging
from decimal import Decimal
import boto3
from botocore.exceptions import ClientError
from config import REGION_NAME

dynamodb     = boto3.resource('dynamodb', region_name=REGION_NAME)
trades_table = dynamodb.Table('Trades')

def write_trade_open(symbol, reason, entry_price, tp, sl, balance_start):
    trade_id  = str(uuid.uuid4())
    open_time = dt.datetime.utcnow().isoformat()
    item = {
        'trade_id':     trade_id,
        'symbol':       symbol,
        'reason':       reason,
        'open_time':    open_time,
        'entry_price':  Decimal(str(entry_price)),
        'tp':           Decimal(str(tp)),
        'sl':           Decimal(str(sl)),
        'balance_start':Decimal(str(balance_start)),
        'closed':       False
    }
    try:
        trades_table.put_item(Item=item)
    except ClientError as e:
        logging.error(f"DynamoDB open write failed: {e}")
    return trade_id

def write_trade_close(trade_id, close_price, pnl, balance_end):
    close_time = dt.datetime.utcnow().isoformat()
    try:
        trades_table.update_item(
            Key={'trade_id': trade_id},
            UpdateExpression="""
                SET close_price = :cp,
                    close_time  = :ct,
                    realised_pnl= :p,
                    balance_end = :be,
                    closed      = :cl
            """,
            ExpressionAttributeValues={
                ':cp': Decimal(str(close_price)),
                ':ct': close_time,
                ':p':  Decimal(str(pnl)),
                ':be': Decimal(str(balance_end)),
                ':cl': True
            }
        )
    except ClientError as e:
        logging.error(f"DynamoDB close write failed: {e}")

if __name__ == '__main__':
   trade_id =  write_trade_open(symbol='SOL/USDT', reason='Asia Swept', entry_price='1234', tp='1330',
                                sl='1200', balance_start='1000')
