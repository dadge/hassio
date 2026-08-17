# OKX Volatility Harvester

A rebalancing bot for OKX spot. It does **not** try to predict prices. It earns
the *volatility harvest*: the growth a portfolio gets from repeatedly selling
whatever went up and buying whatever went down, back to fixed weights.

Runs in **paper mode by default**. Real trading requires two deliberate
configuration changes and a restart.

---

## Why this instead of a signal strategy

For an asset with log-drift `nu` and volatility `sigma`, a portfolio rebalanced
to a constant fraction `w` in the asset and `1-w` in cash grows at

```
g(w) = w·(nu + sigma²/2) − w²·sigma²/2
```

The second term is the harvest. If `nu = 0` it leaves `(sigma²/2)·w·(1−w)`,
maximised at `w = ½`, giving **sigma²/8 > 0** — positive growth from volatility
alone, forecasting nothing.

This matters because a screen of 51 community Freqtrade strategies over 8
months of OKX data (40 pairs, fees included) found **no** forecasting edge that
survived validation: the eight that looked profitable were either reading
future candles or profitable in only one half of the period. The harvest, by
contrast, was positive in every configuration tested, at every split date, in
both a rising and a falling market, and at fee levels up to 2% per side.

### What it will and will not do

The harvest is **relative**. Measured on that data it turned:

| market | same basket, held | rebalanced |
|---|---|---|
| falling half | −14.0% | **−10.6%** |
| rising half | +13.5% | **+24.2%** |

It reliably beats holding the same assets. **It does not make a falling market
profitable.** If the assets you hold fall, you lose money — just less of it.
No rebalancing rule can change that, and any bot claiming otherwise is
mismeasuring something.

---

## How it works

1. **Select** — ranks liquid USDT spot pairs by trailing realised volatility
   and takes the top `basket_size`. Re-selects every `reselect_days`.
2. **Target** — equal weight per asset, totalling `target_exposure_pct` of the
   wallet; the remainder stays in USDT.
3. **Rebalance** — every `check_interval_minutes` it measures each leg's drift
   from target. If any leg is more than `rebalance_band_pct` away, all legs are
   traded back to target (sells first, so the quote balance exists before buys).

### Why selection uses volatility and never past returns

On the test data, volatility persisted strongly between adjacent periods (rank
correlation **+0.88**) while drift barely did (**+0.16**). Ranking on past
returns would pick whatever recently went up, which is hindsight — the exact
error that made those 51 strategies look profitable. Volatility is the part
that is actually predictable, and it is the part the harvest formula depends on.

---

## Options

| Option | Default | Meaning |
|---|---|---|
| `mode` | `dry-run` | `dry-run` = paper. `live` = real orders. |
| `i_understand_live_trading` | `false` | Must be `true` for `live` to start. |
| `okx_environment` | `okx` | `myokx` for the EEA entity. **Hostname only** — it does not change which pairs are traded. |
| `quote_currency` | `USDT` | Currency the book is priced in; only pairs quoted in it are traded. |
| `okx_api_key` / `_secret` / `_passphrase` | empty | Required for `live` only. |
| `basket_size` | `10` | Assets held. Below ~5, one asset's drift dominates. |
| `target_exposure_pct` | `50` | % of wallet in crypto; rest is cash. |
| `rebalance_band_pct` | `1.0` | Drift (in points of the whole book) that triggers a rebalance. |
| `volatility_lookback_days` | `30` | Window for ranking volatility. |
| `reselect_days` | `30` | How often the basket is re-chosen. |
| `min_volume_usdt` | `5000000` | Liquidity floor. Raise it if you see slippage. |
| `min_order_usdt` | `5` | Legs smaller than this are skipped. |
| `paper_wallet_usdt` | `1000` | Starting paper balance. |
| `paper_slippage_model` | `orderbook` | How paper fills are priced: `orderbook` (real spread + impact), `fixed`, or `none`. |
| `paper_slippage_pct` | `0.1` | Percentage charged per fill when the model is `fixed`. |
| `live_max_deployed_usdt` | `100` | **Hard cap on money at risk in live mode.** |
| `check_interval_minutes` | `15` | How often drift is measured. |

### Paper trading and slippage

Paper mode used to fill at the last traded price, which assumes no spread and
infinite depth. That is roughly harmless on deep books and badly misleading on
thin ones — and this strategy deliberately seeks out the most volatile names,
which are the thinnest. A paper run could show a profit that live could not
reproduce.

By default paper fills now **walk the real order book** for the size being
traded, charging the actual spread and the impact of your own order, and the
panel reports slippage separately from fees. Fees are a known constant; slippage
is the unknown, and separating them is the point.

If slippage is comparable to the harvest, that configuration is not viable at
that size — lower `basket_size`, raise `min_volume_usdt`, or trade a deeper
quote currency. Live trading is unaffected by these settings: real fills carry
real slippage by construction.

### Choosing `quote_currency`

`USDT` or `USDC`. This is the only setting that decides which pairs are traded
— `okx_environment` merely selects the API hostname (`my.okx.com` for the EEA
entity) and has no bearing on it.

OKX lists far fewer USDC spot pairs than USDT ones, so a USDC basket is picked
from a much smaller universe, which weakens the selection: the whole point is to
choose the most volatile names out of a wide field. After switching, check the
add-on log for the line reporting how many pairs survived the volume filter —
if it is not comfortably larger than `basket_size`, the bot is holding
near-enough whatever exists rather than the most volatile names, and you should
lower `min_volume_usdt` or stay on USDT.

The `*_usdt` option names are historical: those amounts are denominated in
whichever quote currency you select.

### Choosing `target_exposure_pct`

Harvest is *quadratic* in exposure; market-drift risk is *linear*. So going
from 50% to 100% roughly doubles your drawdown but does not double the harvest.
50% is the mathematical sweet spot for a zero-drift asset, and lower is the
conservative direction.

### Choosing `rebalance_band_pct`

Tighter bands harvest slightly more and pay fees much more often. Measured on
the test data, moving from 0.1% to 2% per side in costs only reduced the
harvest from +10.7pp to +7.1pp, because a 1% band triggers just ~50 rebalances
in four months. **1% is a good default**; below 0.5% you are mostly paying fees.

---

## Going live

Paper first. Let it run long enough to see several rebalances and satisfy
yourself the behaviour is sane.

1. Create an OKX API key with **trade** permission — not withdrawal.
   Restrict it to your IP if you can.
2. In the **Configuration** tab set:
   - `okx_api_key`, `okx_api_secret`, `okx_api_passphrase`
   - `live_max_deployed_usdt` — start small; this is your real cap
   - `mode: live`
   - `i_understand_live_trading: true`
3. Save and **restart** the add-on.

The log will print a `*** LIVE ***` banner and the panel turns red.

### Safety behaviour

- **Paper is the default**, and an empty/partial credential set can never trade.
- **Every add-on update forces `dry-run` back on**, exactly like the Freqtrade
  add-on. A new version never inherits live trading; you must opt in again.
- **`live_max_deployed_usdt` is enforced in the sizing itself** — if your wallet
  grows, order sizes stay capped.
- **Reset is disabled in live mode**: erasing a book whose positions still exist
  on the exchange would make the panel lie to you.
- The bot places **market orders**. On thin books that costs spread; keep
  `min_volume_usdt` high enough that your basket stays liquid.

---

## Panel

Reachable from the Home Assistant sidebar (**Harvester**).

- **Banner** — paper (blue) or live (red), plus paused state.
- **KPIs** — equity, P&L against the starting wallet, cash, actual vs target
  exposure, rebalance count, cumulative fees.
- **Equity chart** — the book's value over time.
- **Basket** — each leg's price, value, actual vs target weight, and a drift bar
  that turns amber once the leg is outside the band.
- **Controls** — pause, resume, force a rebalance, reset the paper portfolio.
- **Activity** — selections, rebalances, errors.

Switching to real trading is deliberately *not* a panel button: it is a
configuration change plus a restart.

---

## Known limits

- Validated on a single 8-month window of OKX spot data. One market, one
  period.
- The universe only contains pairs listed today; assets that were delisted are
  invisible to the backtest that motivated this design.
- High-volatility assets are usually small caps. Real spread and market impact
  can exceed the modelled fee — that is what `min_volume_usdt` defends against.
- Market orders only. No limit orders, no maker rebates.
