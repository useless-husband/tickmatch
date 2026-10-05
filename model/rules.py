"""Price grid of one trading day: tick bands, daily limits, reference price.

Every function names the section of docs/RULES.md it implements.  Prices are
integers in cents (0.01 TWD).
"""
from fractions import Fraction

# R2: (upper bound of the band in cents, exclusive; tick in cents)
TICK_BANDS = [(1000, 1), (5000, 5), (10000, 10), (50000, 50), (100000, 100), (None, 500)]
MIN_PRICE = 1  # one cent, the smallest tick (R3)


def tick_of(price):
    """R2: tick size of the band that contains `price`."""
    for upper, tick in TICK_BANDS:
        if upper is None or price < upper:
            return tick
    raise AssertionError


def is_valid_price(price):
    """R2: a price is valid when it is a multiple of its own band's tick."""
    return price >= MIN_PRICE and price % tick_of(price) == 0


def floor_to_tick(x):
    """Largest valid price <= x (x may be a Fraction)."""
    p = int(x // 1)
    t = tick_of(p)
    return p - p % t


def ceil_to_tick(x):
    """Smallest valid price >= x (x may be a Fraction)."""
    p = -int((-x) // 1)
    t = tick_of(p)
    return p if p % t == 0 else p + t - p % t


def daily_limits(ref, pct=10):
    """R3: (limit_down, limit_up) for a reference price.

    Limit-up is the largest valid tick not above ref*(1+pct%), limit-down the
    smallest valid tick not below ref*(1-pct%).  If 10% is smaller than one
    tick, the limit is one tick, and the price never goes below the minimum
    tick.
    """
    assert is_valid_price(ref), ref
    fluct = Fraction(ref * pct, 100)
    up = floor_to_tick(ref + fluct)
    dn = ceil_to_tick(ref - fluct)
    if up == ref:                       # 10% is less than a tick: one tick up
        up = ref + tick_of(ref)
    if dn == ref and ref > MIN_PRICE:   # ... and one tick down, never below the minimum
        dn = ref - tick_of(ref - 1)
    return max(dn, MIN_PRICE), up


def price_levels(ref, pct=10):
    """R2+R3: every valid price inside the daily limits, ascending."""
    dn, up = daily_limits(ref, pct)
    out, p = [], dn
    while p <= up:
        out.append(p)
        p += tick_of(p)
    return out


def next_reference_price(ref, close, best_bid_at_close, best_ask_at_close):
    """R3.1: the next day's auction reference price (OR Art. 58-3 para. 4)."""
    if close is not None:
        return close
    if best_bid_at_close is not None and best_bid_at_close > ref:
        return best_bid_at_close
    if best_ask_at_close is not None and best_ask_at_close < ref:
        return best_ask_at_close
    return ref
