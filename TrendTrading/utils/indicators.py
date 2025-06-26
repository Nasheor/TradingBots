import numpy as np

def ema(series, period: int):
    """
    Exponential Moving Average.
    `series` can be a list or 1-D numpy array of floats.
    Returns a numpy array the same length as `series`.
    """
    series = np.asarray(series, dtype=float)
    if series.ndim != 1:
        raise ValueError("EMA expects a 1-D array")

    alpha = 2 / (period + 1)
    ema = np.empty_like(series)
    ema[0] = series[0]          # seed with first value

    for i in range(1, len(series)):
        ema[i] = alpha * series[i] + (1 - alpha) * ema[i - 1]

    return ema
