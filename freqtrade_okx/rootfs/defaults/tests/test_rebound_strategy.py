"""Unit tests for ReboundStrategy's indicator / scoring logic.

Run INSIDE the add-on container (it needs freqtrade, pandas and TA-Lib):

    docker exec -it addon_XXX_freqtrade_okx python3 /data/user_data/tests/test_rebound_strategy.py

or, if pytest is installed:

    docker exec -it addon_XXX_freqtrade_okx python3 -m pytest /data/user_data/tests -q

(the `ft-test-strategy` helper inside the container wraps exactly this)

The tests build synthetic OHLCV dataframes following Freqtrade's testing
patterns: a flat phase, a controlled -20% dip, then a rebound with rising
volume — and assert that the entry signal fires only where it should.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# Make the strategy importable regardless of where the tests live.
for candidate in (
    Path(__file__).resolve().parents[1] / "strategies",
    Path("/data/user_data/strategies"),
    Path("/defaults/strategies"),
):
    if (candidate / "ReboundStrategy.py").exists():
        sys.path.insert(0, str(candidate))
        break

from ReboundStrategy import ReboundStrategy  # noqa: E402

METADATA = {"pair": "BTC/USDT"}

FLAT_LEN = 40      # candles 0..39: flat around 100
DIP_LEN = 20       # candles 40..59: -1.2% each -> bottom ~78.5 (-21%)
REBOUND_LEN = 5    # candles 60..64: +2.1% each with rising volume
TAIL_LEN = 10      # candles 65..74: drifting sideways


def make_strategy() -> ReboundStrategy:
    return ReboundStrategy(config={})


def make_ohlcv(closes, volumes) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate(([closes[0]], closes[:-1]))
    highs = np.maximum(opens, closes) * 1.002
    lows = np.minimum(opens, closes) * 0.998
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=len(closes), freq="1h", tz="UTC"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.asarray(volumes, dtype=float),
        }
    )


def make_dip_rebound_df() -> pd.DataFrame:
    closes, volumes = [], []
    price = 100.0
    # Flat phase: tiny deterministic wiggle, alternating volume.
    for i in range(FLAT_LEN):
        price = 100.0 + 0.2 * ((-1) ** i)
        closes.append(price)
        volumes.append(1000.0 + 10 * (i % 3))
    # Dip phase: steady -1.2% candles on average volume.
    for _ in range(DIP_LEN):
        price *= 0.988
        closes.append(price)
        volumes.append(1100.0)
    # Rebound phase: consecutive higher closes with rising volume.
    for i in range(REBOUND_LEN):
        price *= 1.021
        closes.append(price)
        volumes.append(1300.0 + 400.0 * i)
    # Tail: sideways.
    for i in range(TAIL_LEN):
        price *= 1.0005 if i % 2 else 0.9995
        closes.append(price)
        volumes.append(1200.0)
    return make_ohlcv(closes, volumes)


def run_strategy(df: pd.DataFrame):
    strategy = make_strategy()
    df = strategy.populate_indicators(df.copy(), METADATA)
    df = strategy.populate_entry_trend(df, METADATA)
    if "enter_long" not in df.columns:
        df["enter_long"] = 0
    return strategy, df


def test_dip_is_detected():
    _, df = run_strategy(make_dip_rebound_df())
    bottom = FLAT_LEN + DIP_LEN - 1
    assert df["drop_pct"].iloc[bottom] >= 0.10, (
        f"expected >=10% drop at the bottom, got {df['drop_pct'].iloc[bottom]:.3f}"
    )
    assert bool(df["dip_ok"].iloc[bottom]), "dip precondition should hold at the bottom"


def test_score_rises_during_rebound():
    _, df = run_strategy(make_dip_rebound_df())
    rebound = slice(FLAT_LEN + DIP_LEN, FLAT_LEN + DIP_LEN + REBOUND_LEN)
    flat = slice(20, FLAT_LEN)  # after indicator warm-up
    assert df["rebound_score"].iloc[rebound].max() >= 4, (
        "weighted score should reach the entry threshold during the rebound, "
        f"got max {df['rebound_score'].iloc[rebound].max()}"
    )
    assert df["rebound_score"].iloc[flat].max() <= 2, (
        "flat phase must not accumulate a high rebound score"
    )


def test_entry_fires_only_in_rebound_window():
    _, df = run_strategy(make_dip_rebound_df())
    entries = df.index[df["enter_long"] == 1].tolist()
    assert entries, "expected at least one entry signal in the dip-rebound scenario"
    lo = FLAT_LEN + DIP_LEN
    hi = FLAT_LEN + DIP_LEN + REBOUND_LEN + 1
    assert all(lo <= i < hi for i in entries), (
        f"entry signals outside the rebound window: {entries} (expected within [{lo}, {hi}))"
    )
    # No entries during the flat phase or while the knife is still falling.
    assert not any(i < lo for i in entries)


def test_no_entry_without_dip():
    """A steady uptrend has higher closes and rising volume but no dip:
    the dip precondition must veto entries."""
    n = 80
    closes = [100.0 * (1.004 ** i) for i in range(n)]
    volumes = [1000.0 + 5.0 * i for i in range(n)]
    _, df = run_strategy(make_ohlcv(closes, volumes))
    assert int((df["enter_long"] == 1).sum()) == 0, "uptrend without dip must not trigger entries"


def test_stoploss_is_never_disabled():
    strategy = make_strategy()
    assert strategy.stoploss < 0, "stoploss must be a negative ratio"
    assert strategy.stoploss > -1, "stoploss must not be effectively disabled (-1)"
    assert strategy.position_adjustment_enable is False


class _FakeTrade:
    """Stand-in for freqtrade's Trade — custom_exit only reads open_date_utc."""

    def __init__(self, age_hours: float):
        self.pair = "BTC/USDT"
        self.open_date_utc = datetime.now(timezone.utc) - timedelta(hours=age_hours)


def test_custom_exit_closes_stale_trades():
    strategy = make_strategy()
    hold = int(strategy.max_hold_hours.value)
    now = datetime.now(timezone.utc)

    # Fresh and just-below-the-limit trades stay open...
    assert strategy.custom_exit("BTC/USDT", _FakeTrade(1), now, 100.0, 0.01) is None
    assert strategy.custom_exit("BTC/USDT", _FakeTrade(hold - 1), now, 100.0, 0.01) is None
    # ...and one past the limit is closed, whether it is up or down.
    assert strategy.custom_exit("BTC/USDT", _FakeTrade(hold + 1), now, 100.0, -0.02) == "max_hold_time"
    assert strategy.custom_exit("BTC/USDT", _FakeTrade(hold + 1), now, 100.0, 0.02) == "max_hold_time"


def _main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"ERROR {name}: {exc!r}")
    print("=" * 40)
    print("ALL TESTS PASSED" if failures == 0 else f"{failures} TEST(S) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
