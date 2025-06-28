import os

from openai import OpenAI
from config import GPT_MODEL

client = OpenAI(api_key=os.getenv("OPEN_API_KEY"))

def gpt_rank_setups(setups):
    prompt = """You are a professional crypto analyst. For each setup below, evaluate trade direction, strength of opportunity based on indicator confluence, and summarize reasoning:
"""
    for s in setups:
        prompt += f"\n- {s['symbol']}: Trend slope={s['slope']:.5f}, Price={s['price']:.2f}, EMA200={s['ema200']:.2f}, RSI={s['rsi']:.2f}, Volume={s['volume']:.0f}, Avg Volume={s['avg_volume']:.0f}, Confluence: {s['confluence']}"

    prompt += "\n\nReturn a ranked list top three and bottom three with reasoning for each."

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "system", "content": "You rank crypto setups with detailed reasoning."}, {"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.strip()
