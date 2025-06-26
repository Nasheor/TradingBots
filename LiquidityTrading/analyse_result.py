import pandas as pd
import matplotlib.pyplot as plt
from decimal import Decimal
# import ace_tools as tools

# Load the backtest results
df = pd.read_csv('/backtest_results/XRP_killzone_backtest.csv', parse_dates=['date'])

# Compute summary metrics
total = len(df)
wins = df[df['result'] == 'TP']
losses = df[df['result'] == 'SL']
ends = df[df['result'] == 'END']

win_rate = len(wins) / total * 100
total_pnl = df['pnl'].sum()
avg_pnl = df['pnl'].mean()
avg_win = wins['pnl'].mean() if not wins.empty else 0
avg_loss = losses['pnl'].mean() if not losses.empty else 0
final_balance = df['balance'].iloc[-1]
equity = df['balance']
roll_max = equity.cummax()
drawdown = equity - roll_max
max_dd = drawdown.min()

# Prepare a summary DataFrame
metrics = pd.DataFrame({
    'Metric': [
        'Total trades',
        'Winning trades',
        'Losing trades',
        'Trades closed at session end',
        'Win rate (%)',
        'Total PnL (USDT)',
        'Final balance (USDT)',
        'Avg PnL per trade (USDT)',
        'Avg win (USDT)',
        'Avg loss (USDT)',
        'Max drawdown (USDT)'
    ],
    'Value': [
        total,
        len(wins),
        len(losses),
        len(ends),
        round(win_rate, 2),
        round(total_pnl, 2),
        round(final_balance, 2),
        round(avg_pnl, 2),
        round(avg_win, 2),
        round(avg_loss, 2),
        round(max_dd, 2)
    ]
})

# Display the summary
# tools.display_dataframe_to_user('SOL Backtest Summary', metrics)

# Plot the equity curve
plt.figure()
plt.plot(df['date'], df['balance'])
plt.title('SOL Equity Curve')
plt.xlabel('Date')
plt.ylabel('Account Balance (USDT)')
plt.xticks(rotation=45)
plt.tight_layout()
plt.show()
