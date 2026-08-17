# Changelog

## 0.3.1

- The bot now warns, in the log and the panel's activity list, when fewer pairs
  qualify than `basket_size` asks for. Switching a live instance to USDC left it
  holding two legs out of ten with no indication anything was wrong: OKX lists
  264 USDC spot pairs but only 3 above a 5,000,000 volume floor, so the ranking
  had nothing to choose between and the basket was filled rather than selected.
- The dry-run startup banner names the configured quote currency instead of
  always printing USDT.

## 0.3.0

- **Changing `quote_currency` now takes effect immediately.** It previously
  applied only at the next scheduled re-selection, so for up to `reselect_days`
  the panel reported the new currency while the bot still held and traded the
  old one -- observed on a live instance, which sat on a USDT basket after being
  switched to USDC. Any leg not quoted in the configured currency now forces a
  re-selection on the next check, and the old legs are liquidated.
- Option descriptions that predated the setting no longer say "USDT" where they
  mean the selected quote currency.

  Switching currency on a **live** book sells the old legs into the old quote
  currency, leaving that balance outside the bot's cash leg. Move or convert it
  yourself; the bot will not touch it.

## 0.2.0

- **New option `quote_currency` (`USDT` or `USDC`).** The quote currency was
  previously hardcoded to USDT with no way to change it. It is separate from
  `okx_environment`, which only selects the API hostname and never affected
  which pairs were traded. Note that OKX lists far fewer USDC spot pairs, so a
  USDC basket is selected from a much smaller universe.
- The panel now labels values with the configured currency instead of always
  printing USDT.
- The basket log line now reports how many pairs existed before the volume
  filter and how many the exchange reported no volume for, so an unexpectedly
  small universe can be told apart from an over-tight filter.
- Add-on linter fixes: dropped `boot` and `ingress_port` (both restated
  defaults) and removed `armv7`, unsupported since Home Assistant 2025.12.
- The container smoke test now polls for the panel instead of sleeping a fixed
  8 seconds, which was long enough on a developer machine but not on a cold CI
  runner where importing ccxt dominates startup. It also dumps the container
  log when the probe fails.

## 0.1.0

Initial release — paper trading by default.

- Constant-weight rebalancing engine for OKX spot: ranks liquid USDT pairs by
  trailing realised volatility, holds the top N at equal weight plus a cash
  leg, and rebalances when any leg drifts outside the band.
- Ingress control panel: equity chart, per-leg drift bars, pause/resume, force
  rebalance, reset paper portfolio.
- Home Assistant notifications on rebalance, mode change, startup and errors.
- Safety: dry-run default, forced dry-run after every add-on update, explicit
  `i_understand_live_trading` confirmation, and `live_max_deployed_usdt` as a
  hard ceiling enforced in order sizing.
- State persisted atomically to `/data/harvest_state.json` so a restart does
  not lose the book; live mode re-syncs holdings from exchange balances.

Design and validation are documented in the Documentation tab. Engine tests
live in `tests/test_harvest_bot.py`.
