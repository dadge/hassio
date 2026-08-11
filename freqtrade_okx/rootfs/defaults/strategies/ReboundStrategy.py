"""ReboundStrategy — dip-rebound entries on the 1h timeframe.

Idea
----
Buy assets that dropped sharply (default: >= 10% over the last 24 hourly
candles) once a *reversal confirmation* appears. Confirmation is a weighted
score over several signals; the entry fires when the score reaches a
threshold while the pair is still depressed:

    +1  two consecutive higher closes
    +1  a third consecutive higher close
    +1  two higher closes accompanied by rising volume
    +2  RSI(14) crossing back above the oversold line (default 30)
    +2  price reclaiming EMA(9) (close crosses above the EMA)

Exits
-----
* ``minimal_roi``: +5% profit target by default.
  NOTE: 50-100% profit targets are configurable here, but they are
  statistically incompatible with a high win rate on 1h dip-rebounds —
  most bounces retrace a few percent, not double. The modest defaults
  are deliberate; validate any change with backtesting first.
* ``stoploss``: -3% — never disabled. Do not set this to -1 (off);
  a rebound strategy without a stop turns every failed bounce into a bag.
* Optional trailing stop (disabled by default, parameters below).
* ``custom_exit``: closes any position held longer than ``max_hold_hours``
  (default 72h) — a dip that has not bounced within 3 days is not a rebound.

Stoploss on exchange
--------------------
OKX supports stoploss-on-exchange in current Freqtrade versions (stop-market
and stop-limit on spot), so it is enabled below: if the bot or the host dies,
the exchange still honours the stop. In dry-run mode Freqtrade simulates it.

Protections (circuit breakers)
------------------------------
* Per-pair 48h cooldown after a stoploss (StoplossGuard, only_per_pair).
* Global stop when 4 stoplosses hit within 48h (StoplossGuard).
* Global stop on excessive drawdown (MaxDrawdown).
* Short cooldown after every exit (CooldownPeriod).

No position adjustment / averaging down: ``position_adjustment_enable`` stays
False — averaging into a falling knife defeats the stoploss.

All thresholds are Freqtrade parameters and usable with hyperopt (buy space).
"""

from datetime import datetime, timedelta
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy


class ReboundStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False

    process_only_new_candles = True
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Never average down.
    position_adjustment_enable = False

    # Enough history for the longest lookback (48) + RSI/EMA warm-up.
    startup_candle_count = 100

    # ---------------------------------------------------------------- exits --
    # Deliberately modest target — see module docstring.
    minimal_roi = {"0": 0.05}

    # Hard stop, never disabled.
    stoploss = -0.03

    # Optional trailing stop: once +3% is reached, trail 2% below the peak.
    # Flip trailing_stop to True to enable (then validate with a backtest).
    trailing_stop = False
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    # OKX supports stoploss on exchange (stop-market / stop-limit on spot).
    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
    }

    # ----------------------------------------------------------- parameters --
    # Dip detection: drop of >= dip_threshold within the last dip_lookback
    # candles (measured from the rolling high to the rolling low).
    dip_lookback = IntParameter(12, 48, default=24, space="buy", optimize=True)
    dip_threshold = DecimalParameter(0.05, 0.30, default=0.10, decimals=2, space="buy", optimize=True)
    # The entry must still be "in the hole": close below
    # recent_high * (1 - dip_threshold * max_recovery). With the defaults the
    # pair must still be >= 5% below its recent high when we buy.
    max_recovery = DecimalParameter(0.2, 0.8, default=0.5, decimals=1, space="buy", optimize=False)

    rsi_oversold = IntParameter(20, 40, default=30, space="buy", optimize=True)
    ema_len = IntParameter(5, 21, default=9, space="buy", optimize=False)

    # Signal weights and the score needed to enter.
    w_two_up = IntParameter(0, 3, default=1, space="buy", optimize=False)
    w_three_up = IntParameter(0, 3, default=1, space="buy", optimize=False)
    w_rising_volume = IntParameter(0, 3, default=1, space="buy", optimize=False)
    w_rsi_cross = IntParameter(0, 3, default=2, space="buy", optimize=False)
    w_ema_reclaim = IntParameter(0, 3, default=2, space="buy", optimize=False)
    entry_score_min = IntParameter(2, 7, default=4, space="buy", optimize=True)

    # Time-based exit (custom_exit below).
    max_hold_hours = IntParameter(12, 168, default=72, space="sell", optimize=False)

    # ----------------------------------------------------------- protections --
    @property
    def protections(self):
        return [
            # Breathe for 2 candles after any exit on a pair.
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            # 48h per-pair cooldown after a single stoploss (1h candles).
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 48,
                "trade_limit": 1,
                "stop_duration_candles": 48,
                "only_per_pair": True,
            },
            # Global circuit breaker: 4 stoplosses within 48h -> pause 24h.
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 48,
                "trade_limit": 4,
                "stop_duration_candles": 24,
                "only_per_pair": False,
            },
            # Global circuit breaker: >12% drawdown over the last week -> pause 48h.
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 168,
                "trade_limit": 5,
                "max_allowed_drawdown": 0.12,
                "stop_duration_candles": 48,
            },
        ]

    # ------------------------------------------------------------ indicators --
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        lookback = int(self.dip_lookback.value)

        dataframe["ema"] = ta.EMA(dataframe, timeperiod=int(self.ema_len.value))
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        dataframe["recent_high"] = dataframe["high"].rolling(lookback, min_periods=lookback).max()
        dataframe["recent_low"] = dataframe["low"].rolling(lookback, min_periods=lookback).min()
        dataframe["drop_pct"] = 1.0 - (dataframe["recent_low"] / dataframe["recent_high"])

        up = dataframe["close"] > dataframe["close"].shift(1)
        two_up = up & up.shift(1).fillna(False)
        three_up = two_up & up.shift(2).fillna(False)

        vol_up = dataframe["volume"] > dataframe["volume"].shift(1)
        rising_volume = two_up & vol_up & vol_up.shift(1).fillna(False)

        oversold = int(self.rsi_oversold.value)
        rsi_cross = (dataframe["rsi"] > oversold) & (dataframe["rsi"].shift(1) <= oversold)

        ema_reclaim = (dataframe["close"] > dataframe["ema"]) & (
            dataframe["close"].shift(1) <= dataframe["ema"].shift(1)
        )

        dataframe["rebound_score"] = (
            two_up.astype(int) * int(self.w_two_up.value)
            + three_up.astype(int) * int(self.w_three_up.value)
            + rising_volume.astype(int) * int(self.w_rising_volume.value)
            + rsi_cross.fillna(False).astype(int) * int(self.w_rsi_cross.value)
            + ema_reclaim.fillna(False).astype(int) * int(self.w_ema_reclaim.value)
        )

        # Dip precondition: big enough drop, and price still depressed.
        dataframe["dip_ok"] = (
            (dataframe["drop_pct"] >= float(self.dip_threshold.value))
            & (
                dataframe["close"]
                <= dataframe["recent_high"]
                * (1.0 - float(self.dip_threshold.value) * float(self.max_recovery.value))
            )
        )
        return dataframe

    # ----------------------------------------------------------------- entry --
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                dataframe["dip_ok"]
                & (dataframe["rebound_score"] >= int(self.entry_score_min.value))
                & (dataframe["volume"] > 0)
            ),
            ["enter_long", "enter_tag"],
        ] = (1, "rebound")
        return dataframe

    # ------------------------------------------------------------------ exit --
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exits are handled by minimal_roi / stoploss / custom_exit.
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> Optional[str]:
        """Time-based exit: a dip that has not rebounded within
        ``max_hold_hours`` is treated as a failed setup and closed."""
        if current_time - trade.open_date_utc >= timedelta(hours=int(self.max_hold_hours.value)):
            return "max_hold_time"
        return None
