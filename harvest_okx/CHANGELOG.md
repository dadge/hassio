# Changelog

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
