"""MeanRevert15m — volatility-gated mean reversion on the 15m timeframe.

Designed for the case this add-on actually runs in: **USDC pairs on OKX's EEA
entity**, where books are thin and the round-trip cost (taker/maker fees plus
crossing the spread) is roughly 0.3-0.6%. That cost, not signal quality, is the
binding constraint, and every choice below follows from it.

Why 15m
-------
Cost is a fraction of the move you are targeting. Against a ~1% move on 5m,
0.3-0.6% eats 30-60% of the gross edge; against a ~2.5% move on 15m it is
12-24%. Going slower still (1h) improves that ratio further but trades so
rarely on a small whitelist that neither the account nor the backtest
accumulates a usable sample.

The trade geometry, stated honestly
-----------------------------------
Target +2.5% (decaying), stop -2.0%, cost ~0.4% round trip:

    win  ≈ +2.5 - 0.4 = +2.1%
    loss ≈ -2.0 - 0.4 = -2.4%
    breakeven win rate = 2.4 / (2.4 + 2.1) ≈ 53%

A trend-filtered mean reversion can plausibly clear 53%. Compare with a design
that stops at -4% and takes +1% profits while averaging down: that needs ~87%,
which nothing sustains. **This is the whole argument for the numbers below** —
do not widen the stop or shrink the target without redoing this arithmetic.

No claim is made about monthly return. Frequency depends entirely on how many
pairs survive your volume/spread filters, so on a 3-pair whitelist this will
trade a handful of times a month whatever its edge.

Entry
-----
All five must hold on a closed candle:

1. **Uptrend** — close above the 1h EMA(50), and that EMA rising. Mean
   reversion inside a downtrend is just catching a falling knife.
2. **Dip** — the close touched the lower Bollinger band (20, 2σ) within the
   last ``bb_touch_lookback`` candles, and price is still in the lower half of
   the band. Note it is *touched recently*, not *touching now*: by the time
   the turn confirms, price has usually left the band, so demanding both on
   one candle produces a strategy that almost never trades.
3. **Turn** — RSI(14) crossing back above the oversold line *and* a higher
   close. Waiting for the turn costs a little entry price and avoids a lot of
   knife-catching.
4. **Volatility floor** — ATR(14)/close at least ``min_atr_percent``. This is
   the cost defence: if a pair does not move meaningfully more than the
   round-trip cost, no signal on it is worth taking.
5. **Participation** — volume above its own recent average.

At order time ``confirm_trade_entry`` additionally rejects the trade if the
live spread is wider than ``max_entry_spread_percent``. A pairlist filter is
evaluated every 30 minutes; spreads on thin books move faster than that.

Exits
-----
``minimal_roi`` (decaying), a hard -2% stop, a trailing stop that arms at
+1.5%, and ``custom_exit`` for two cases: the position has been held longer
than ``max_hold_hours``, or the 1h trend it was bought into has broken while
the trade is under water.

No position adjustment: averaging down doubles exposure exactly when the
premise has been proven wrong.
"""

from datetime import datetime, timedelta
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy, informative


class MeanRevert15m(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    can_short = False

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Averaging down is what turns a bad trade into a bad month.
    position_adjustment_enable = False

    # 1h EMA(50) needs 50 hours = 200 15m candles; the rest need ~30.
    startup_candle_count = 260

    # ---------------------------------------------------------------- exits --
    # See the module docstring before changing any of these three together.
    minimal_roi = {
        "0": 0.025,     # +2.5% immediately
        "240": 0.015,   # +1.5% after 4h
        "720": 0.008,   # +0.8% after 12h
    }
    stoploss = -0.02

    trailing_stop = True
    trailing_stop_positive = 0.007          # trail 0.7% behind the peak...
    trailing_stop_positive_offset = 0.015   # ...once +1.5% is reached
    trailing_only_offset_is_reached = True

    # OKX supports stoploss-on-exchange for spot (stop-market / stop-limit),
    # so the stop survives the bot or the host dying.
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
    }

    # ----------------------------------------------------------- parameters --
    bb_period = IntParameter(14, 30, default=20, space="buy", optimize=False)
    bb_std = DecimalParameter(1.6, 3.0, default=2.0, decimals=1, space="buy", optimize=True)
    # How long a band touch stays "fresh". Too short and the turn is missed;
    # too long and the dip is ancient history by the time we buy.
    bb_touch_lookback = IntParameter(1, 10, default=6, space="buy", optimize=True)
    rsi_oversold = IntParameter(20, 40, default=32, space="buy", optimize=True)
    trend_ema_len = IntParameter(20, 100, default=50, space="buy", optimize=False)

    # Cost defence: skip pairs whose 15m ATR is small relative to price, and
    # skip individual entries when the book is wide at that moment.
    min_atr_percent = DecimalParameter(0.2, 2.0, default=0.6, decimals=1, space="buy", optimize=True)
    max_entry_spread_percent = DecimalParameter(
        0.05, 1.0, default=0.25, decimals=2, space="buy", optimize=False
    )
    volume_factor = DecimalParameter(0.5, 3.0, default=1.0, decimals=1, space="buy", optimize=True)

    max_hold_hours = IntParameter(4, 72, default=24, space="sell", optimize=False)
    exit_on_trend_break = IntParameter(0, 1, default=1, space="sell", optimize=False)

    # ---------------------------------------------------------- protections --
    @property
    def protections(self):
        # Candle counts, so 15m: 4 candles = 1h, 96 candles = 24h.
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 4},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 96,
                "trade_limit": 1,
                "stop_duration_candles": 24,
                "only_per_pair": True,
            },
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 96,
                "trade_limit": 4,
                "stop_duration_candles": 48,
                "only_per_pair": False,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 672,   # one week
                "trade_limit": 10,
                "max_allowed_drawdown": 0.12,
                "stop_duration_candles": 96,
            },
        ]

    # ------------------------------------------------------------ 1h trend ---
    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Columns produced here are merged as ``<name>_1h`` before
        ``populate_indicators`` runs."""
        dataframe["trend_ema"] = ta.EMA(dataframe, timeperiod=int(self.trend_ema_len.value))
        # Rising over the last 4 hours — a flat or rolling-over EMA is not an
        # uptrend, and dips inside those are the expensive ones.
        dataframe["trend_rising"] = dataframe["trend_ema"] > dataframe["trend_ema"].shift(4)
        return dataframe

    # ----------------------------------------------------------- indicators --
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        period = int(self.bb_period.value)
        std = float(self.bb_std.value)

        bb = ta.BBANDS(dataframe, timeperiod=period, nbdevup=std, nbdevdn=std)
        dataframe["bb_lower"] = bb["lowerband"]
        dataframe["bb_middle"] = bb["middleband"]

        # A band touch stays valid for a few candles: the RSI turn that
        # confirms the reversal almost always lands after price has climbed
        # back inside the band.
        touched = (dataframe["close"] <= dataframe["bb_lower"]).astype(int)
        lookback = int(self.bb_touch_lookback.value)
        dataframe["dip_recent"] = touched.rolling(lookback, min_periods=1).max() > 0

        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_percent"] = 100.0 * dataframe["atr"] / dataframe["close"]
        dataframe["volume_mean"] = dataframe["volume"].rolling(20, min_periods=20).mean()

        oversold = int(self.rsi_oversold.value)
        dataframe["rsi_turn"] = (dataframe["rsi"] > oversold) & (
            dataframe["rsi"].shift(1) <= oversold
        )
        dataframe["higher_close"] = dataframe["close"] > dataframe["close"].shift(1)
        return dataframe

    # ----------------------------------------------------------------- entry --
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        trend_ok = (dataframe["close"] > dataframe["trend_ema_1h"]) & dataframe[
            "trend_rising_1h"
        ].fillna(False).astype(bool)

        dataframe.loc[
            (
                trend_ok
                & dataframe["dip_recent"].fillna(False)
                # Still in the lower half of the band: once price is back at
                # the mean the move is over — that is where this strategy
                # sells, not where it buys.
                & (dataframe["close"] < dataframe["bb_middle"])
                & dataframe["rsi_turn"].fillna(False)
                & dataframe["higher_close"].fillna(False)
                & (dataframe["atr_percent"] >= float(self.min_atr_percent.value))
                & (dataframe["volume"] > dataframe["volume_mean"] * float(self.volume_factor.value))
                & (dataframe["volume"] > 0)
            ),
            ["enter_long", "enter_tag"],
        ] = (1, "bb_reversion")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Reversion complete: price back at the mean with momentum spent.
        dataframe.loc[
            (
                (dataframe["close"] >= dataframe["bb_middle"])
                & (dataframe["rsi"] > 70)
                & (dataframe["volume"] > 0)
            ),
            ["exit_long", "exit_tag"],
        ] = (1, "reverted")
        return dataframe

    # ------------------------------------------------------- entry guardrail --
    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> bool:
        """Reject the entry if the book is too wide right now.

        The pairlist's SpreadFilter only re-evaluates every refresh period; on
        thin USDC books the spread at order time is what you actually pay.
        Backtesting has no orderbook, so this check is live/dry-run only and
        the backtest is correspondingly optimistic — a gap worth remembering
        when comparing the two.
        """
        max_spread = float(self.max_entry_spread_percent.value)
        try:
            book = self.dp.orderbook(pair, 1)
            bid, ask = book["bids"][0][0], book["asks"][0][0]
        except (KeyError, IndexError, TypeError, AttributeError, ValueError):
            return True  # no book available (backtest/hyperopt): do not block

        if not bid or not ask or ask <= bid:
            return True
        spread_pct = 100.0 * (ask - bid) / ask
        if spread_pct > max_spread:
            self.dp.send_msg(
                f"{pair}: entry skipped, spread {spread_pct:.2f}% > {max_spread:.2f}%"
            )
            return False
        return True

    # ------------------------------------------------------------------ exit --
    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Optional[str]:
        held = current_time - trade.open_date_utc
        if held >= timedelta(hours=int(self.max_hold_hours.value)):
            return "max_hold_time"

        # The premise was "dip inside an uptrend". If the uptrend is gone and
        # the trade is under water, stop hoping and let the next setup have the
        # capital. Profitable trades are left to ROI/trailing.
        if int(self.exit_on_trend_break.value) and current_profit < 0:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if df is not None and len(df) > 0:
                last = df.iloc[-1]
                trend_ema = last.get("trend_ema_1h")
                if trend_ema is not None and trend_ema == trend_ema:  # not NaN
                    if last["close"] < trend_ema and held >= timedelta(hours=2):
                        return "trend_broken"
        return None
