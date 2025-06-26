from datetime import datetime, timezone

def now_utc(fmt: str | None = None):
    """
    Return a timezone-aware UTC timestamp.
    * If `fmt` is None      → `datetime` object.
    * If `fmt == "ms"`      → Unix epoch in **milliseconds** (int).
    * Otherwise             → formatted string via `strftime(fmt)`.
    """
    dt = datetime.now(timezone.utc)

    if fmt is None:
        return dt
    if fmt.lower() == "ms":
        return int(dt.timestamp() * 1_000)
    return dt.strftime(fmt)
