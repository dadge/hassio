"""Unit tests for MeanRevert15m's indicator / entry logic.

Run INSIDE the add-on container (needs freqtrade, pandas and TA-Lib):

    docker exec -it addon_XXX_freqtrade_okx ft-test-strategy

The dataframes are synthetic and deterministic: an uptrend, a sharp dip inside
it, then a bounce. The tests assert that the entry fires on the bounce and, at
least as importantly, that each guard can veto it on its own — a filter that
never actually blocks anything is not a filter.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

for candidate in (
    Path(__file__).resolve().parents[1] / "strategies",
    Path("/data/user_data/strategies"),
    Path("/defaults/strategies"),
):
    if (candidate / "MeanRevert15m.py").exists():
        sys.path.insert(0, str(candidate))
        break

from MeanRevert15m import MeanRevert15m  # noqa: E402

METADATA = {"pair": "BTC/USDC"}

TREND_LEN = 120   # slow uptrend
DIP_LEN = 6       # sharp drop
BOUNCE_LEN = 4    # recovery
TAIL_LEN = 20


def make_strategy() -> MeanRevert15m:
    return MeanRevert15m(config={})


def make_ohlcv(closes, volumes, trend_ema=None, trend_rising=True) -> pd.DataFrame:
    """Build a 15m dataframe including the columns freqtrade's @informative
    decorator merges in (``trend_ema_1h`` / ``trend_rising_1h``)."""
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate(([closes[0]], closes[:-1]))
    highs = np.maximum(opens, closes) * 1.001
    lows = np.minimum(opens, closes) * 0.999
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(closes), freq="15min", tz="UTC"),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": np.asarray(volumes, dtype=float),
    })
    # Trend reference well below price, so the trend filter passes by default.
    df["trend_ema_1h"] = closes * 0.97 if trend_ema is None else trend_ema
    df["trend_rising_1h"] = trend_rising
    return df


def make_dip_bounce(volume_boost: float = 3.0):
    closes, volumes = [], []
    price = 100.0
    for i in range(TREND_LEN):                     # calm drift up
        price *= 1.0008 if i % 3 else 0.9996
        closes.append(price)
        volumes.append(1000.0 + 5 * (i % 4))
    for _ in range(DIP_LEN):                       # sharp dip, below the band
        price *= 0.985
        closes.append(price)
        volumes.append(1500.0)
    for i in range(BOUNCE_LEN):                    # the turn
        price *= 1.012
        closes.append(price)
        volumes.append(1000.0 * volume_boost + 200.0 * i)
    for i in range(TAIL_LEN):
        price *= 1.0004 if i % 2 else 0.9998
        closes.append(price)
        volumes.append(1100.0)
    return closes, volumes


def run(df: pd.DataFrame):
    strategy = make_strategy()
    df = strategy.populate_indicators(df.copy(), METADATA)
    df = strategy.populate_entry_trend(df, METADATA)
    if "enter_long" not in df.columns:
        df["enter_long"] = 0
    df["enter_long"] = df["enter_long"].fillna(0)
    return strategy, df


def entries(df) -> list:
    return df.index[df["enter_long"] == 1].tolist()


# --------------------------------------------------------------------- tests --
def test_entry_fires_on_the_bounce():
    closes, volumes = make_dip_bounce()
    _, df = run(make_ohlcv(closes, volumes))
    got = entries(df)
    assert got, "expected an entry during the bounce"
    lo, hi = TREND_LEN + DIP_LEN, TREND_LEN + DIP_LEN + BOUNCE_LEN + 1
    assert all(lo <= i <= hi for i in got), f"entries outside the bounce window: {got}"


def test_no_entry_without_a_dip():
    """A steady uptrend never touches the lower band, so nothing should fire."""
    n = 160
    closes = [100.0 * (1.002 ** i) for i in range(n)]
    volumes = [2000.0] * n
    _, df = run(make_ohlcv(closes, volumes))
    assert not entries(df), "an uptrend with no dip must not trigger entries"


def test_downtrend_vetoes_the_entry():
    """Same dip and bounce, but price below a falling 1h EMA: no entry.
    Mean reversion inside a downtrend is the expensive failure mode."""
    closes, volumes = make_dip_bounce()
    df = make_ohlcv(closes, volumes,
                    trend_ema=np.asarray(closes) * 1.05,   # price below the EMA
                    trend_rising=False)
    _, df = run(df)
    assert not entries(df), "entries must be vetoed when the 1h trend is down"


def test_flat_trend_vetoes_the_entry():
    """Price above the EMA but the EMA not rising — still no entry."""
    closes, volumes = make_dip_bounce()
    _, df = run(make_ohlcv(closes, volumes, trend_rising=False))
    assert not entries(df), "a non-rising 1h EMA must veto the entry"


def test_volatility_floor_vetoes_quiet_pairs():
    """The cost defence: if ATR% is below the floor, no signal is worth taking.
    Raising the floor above the fixture's volatility must remove every entry."""
    closes, volumes = make_dip_bounce()
    df_in = make_ohlcv(closes, volumes)
    strategy = make_strategy()
    strategy.min_atr_percent.value = 99.0
    df = strategy.populate_entry_trend(strategy.populate_indicators(df_in.copy(), METADATA), METADATA)
    assert "enter_long" not in df.columns or (df["enter_long"].fillna(0) == 1).sum() == 0, \
        "an unreachable ATR floor must block all entries"


def test_low_volume_vetoes_the_entry():
    closes, volumes = make_dip_bounce(volume_boost=0.2)   # bounce on thin volume
    _, df = run(make_ohlcv(closes, volumes))
    assert not entries(df), "a bounce without participation must not trigger"


def test_geometry_is_not_accidentally_inverted():
    """The design argument depends on target > stop; guard it explicitly."""
    s = make_strategy()
    first_roi = s.minimal_roi[min(s.minimal_roi, key=lambda k: int(k))]
    assert s.stoploss < 0, "stoploss must be negative"
    assert s.stoploss > -1, "stoploss must not be disabled"
    assert first_roi > abs(s.stoploss), (
        f"initial ROI target ({first_roi}) must exceed the stop ({abs(s.stoploss)}), "
        "otherwise the breakeven win rate exceeds 50%"
    )
    assert s.position_adjustment_enable is False, "averaging down is deliberately off"
    assert s.order_types["stoploss_on_exchange"] is True


class _FakeTrade:
    def __init__(self, age_hours: float):
        self.pair = "BTC/USDC"
        self.open_date_utc = datetime.now(timezone.utc) - timedelta(hours=age_hours)


def test_custom_exit_closes_stale_trades():
    s = make_strategy()
    s.dp = None                      # no dataframe access needed for the time exit
    hold = int(s.max_hold_hours.value)
    now = datetime.now(timezone.utc)
    assert s.custom_exit("BTC/USDC", _FakeTrade(1), now, 100.0, 0.005) is None
    assert s.custom_exit("BTC/USDC", _FakeTrade(hold + 1), now, 100.0, -0.01) == "max_hold_time"


def test_confirm_trade_entry_rejects_a_wide_book():
    """The pairlist filter runs every 30 minutes; the spread at order time is
    what is actually paid, so a wide book must block the entry."""
    s = make_strategy()
    now = datetime.now(timezone.utc)

    class DP:
        def __init__(self, bid, ask):
            self.book = {"bids": [[bid, 1.0]], "asks": [[ask, 1.0]]}
            self.messages = []

        def orderbook(self, pair, depth):
            return self.book

        def send_msg(self, msg):
            self.messages.append(msg)

    s.dp = DP(100.0, 100.05)         # 0.05% spread
    assert s.confirm_trade_entry("BTC/USDC", "limit", 1.0, 100.0, "gtc", now, None, "buy") is True

    s.dp = DP(100.0, 101.0)          # ~1% spread
    assert s.confirm_trade_entry("BTC/USDC", "limit", 1.0, 100.0, "gtc", now, None, "buy") is False
    assert s.dp.messages, "a rejected entry should say why"


def test_confirm_trade_entry_allows_when_no_orderbook():
    """Backtesting has no orderbook — the guard must not block everything."""
    s = make_strategy()

    class DP:
        def orderbook(self, pair, depth):
            raise AttributeError("no orderbook in backtesting")

    s.dp = DP()
    assert s.confirm_trade_entry(
        "BTC/USDC", "limit", 1.0, 100.0, "gtc", datetime.now(timezone.utc), None, "buy"
    ) is True


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
