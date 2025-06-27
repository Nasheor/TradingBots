# ─── File: analyse_result.py ───

from openai import OpenAI
from config import GPT_MODEL
import os

client = OpenAI(api_key=os.getenv("OPEN_API_KEY"))

def gpt_rank_setups(setups):
    prompt = """You are a professional crypto analyst. Rank these setups based on quality and potential:
"""
    for s in setups:
        prompt += f"\n- {s['symbol']}: 15m slope={s['trend_slope']:.5f}, 2M close={s['2m_close']:.2f}, EMA200={s['2m_ema200']:.2f}, RSI={s['RSI']:.2f}"

    prompt += "\n\nProvide ranked list with reasoning."

    response = client.chat.completions.create(
        model=GPT_MODEL,
        messages=[{"role": "system", "content": "You rank crypto setups."}, {"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.strip()