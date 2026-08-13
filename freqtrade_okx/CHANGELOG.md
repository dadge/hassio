# Changelog

## 1.3.0

- **New option `strategy`** — pick which strategy the bot runs, including a
  `.py` you drop into `/data/user_data/strategies/` yourself. An unknown name
  is refused at startup with the list of available strategies, rather than a
  freqtrade stack trace half a minute later.
- **New bundled strategy `MeanRevert15m`** — 15m mean reversion built for thin
  USDC books on OKX's EEA entity, where the round-trip cost (~0.3-0.6%) is the
  binding constraint rather than signal quality:
  - target +2.5% decaying, stop -2.0%, so the breakeven win rate is ~53%
    rather than the ~87% a -4% stop with +1% targets and averaging down needs;
  - an ATR volatility floor: no entry unless the pair moves enough to clear
    costs;
  - an orderbook spread check at order time (`confirm_trade_entry`), because a
    pairlist filter only re-evaluates every refresh period;
  - entry on a *recent* band touch plus an RSI turn, not both on one candle —
    by the time the turn confirms, price has normally left the band, and
    requiring simultaneity produces a strategy that never trades;
  - no averaging down.
  Its unit tests assert that every guard can veto an entry on its own.
- `ReboundStrategy` remains the default; nothing changes unless you switch.

## 1.2.1

- **Fix: a failed data download reported only "exit 2" with no explanation.**
  `ft-download-data` discarded freqtrade's stderr when resolving the pairlist,
  and under `set -e` the failing command substitution aborted the script before
  its own error message could print. Exit 2 is freqtrade's code for an
  OperationalException — so there *was* a message, and the helper threw it
  away. It is now quoted verbatim, with the usual causes listed (exchange
  unreachable, USDT asked for on an EEA account, or no pair passing the
  filters).
- New `tests/test_ft_helpers.sh` covering the ft-* helpers directly.

## 1.2.0

- **Backtesting from the sidebar panel.** A new Backtesting card downloads
  data and runs a backtest, showing live progress, the tail of the log, and a
  summary of the finished run (trades, win rate, profit factor, total profit,
  max drawdown). It flags a run with fewer than 20 trades as too small to
  judge. Freqtrade's own backtest API is webserver-mode only, so this is
  driven by a small control endpoint bound to `127.0.0.1` and reachable only
  through ingress; every request field is whitelisted before a process is
  started, and one job runs at a time.
- `ft-download-data` and `ft-backtest` accept `--stake-currency USDT|USDC`, so
  the strategy can be validated on liquid USDT history even when the bot is
  configured to trade USDC.
- **New option `pairlist_max_spread_percent`** (default 0.5), previously
  hardcoded. On thin USDC books a 0.5% cap can cut the whitelist to a couple
  of pairs; the add-on now warns when USDC is combined with a tight cap.
- If the control endpoint fails to start, the add-on says so and keeps
  trading — a panel feature must never stop the bot.

## 1.1.0

- **New option `stake_currency` (`USDT` or `USDC`, default `USDT`).** OKX's
  EEA entity (`my.okx.com`, `okx_environment: myokx`) restricts USDT trading
  under MiCA, and its public API still serves USDT tickers, so dry-run looks
  perfectly healthy while live orders would be rejected. The EUR conversion
  follows the choice (`USDT-EUR` or `USDC-EUR`, inverted) and the blacklist
  adapts. See DOCS §3.1 before going live on an EU account.
- **Renamed** `dry_run_wallet_usdt` → `dry_run_wallet` and
  `pairlist_min_volume_usdt` → `pairlist_min_volume`, since neither is
  USDT-specific any more. **You must re-enter these two values after
  updating** — the Supervisor drops options that are no longer in the schema.
  Lower `pairlist_min_volume` if you switch to USDC: those books are far
  thinner.
- Blacklist now also covers `USDT`, `RLUSD`, `USDG`, `USD0`, `USDS`, `USDD`,
  `LUSD` and `FRAX` as base currencies. A stablecoin cannot dip 10% unless it
  *depegs*, and a depeg is precisely the "dip" this strategy must never buy.

## 1.0.2

- **Fix: the add-on started, then crashed with `ModuleNotFoundError: No module
  named 'freqtrade'`.** The base image installs Freqtrade with
  `pip install -e . --user` as `ftuser`, so the package lives in that user's
  site-packages as an editable install. The add-on runs as root (needed for
  nginx and `/data`), whose user site is `/root/.local` — the `freqtrade`
  command was on `PATH` but the package was not importable. Fixed with
  `PYTHONUSERBASE=/home/ftuser/.local`, which relocates the user site so
  Python evaluates the editable-install hooks. `PYTHONPATH` would *not* work
  here: it appends to `sys.path` without processing `.pth` files.
- The image build now fails if Freqtrade is not importable as root, instead of
  producing an add-on that dies at startup.
- `run.sh` verifies `freqtrade --version` before the exchange-rate lookup, so
  a packaging fault is reported in a second with a clear message rather than
  after three minutes of retries followed by a traceback. The version is
  logged at startup.

## 1.0.1

- **Fix: the add-on could not start.** The EUR→USDT rate was read from a
  `EUR-USDT` ticker, which does not exist on OKX (error 51001, empty data);
  the correct instrument is `USDT-EUR`, whose quote is now inverted. Neither
  `EUR-USDC` nor `EUR-USDT` exists on OKX.
- **Fix:** the ECB fallback pointed at `api.frankfurter.app`, which now
  301-redirects to `api.frankfurter.dev/v1`; curl did not follow redirects,
  so the fallback silently failed too. Now uses the current endpoint, and
  follows redirects.
- Rate-fetch logging now names which source failed and why.
- Regression tests for all of the above.

## 1.0.0

- Initial release.
- Freqtrade pinned to `2026.7` (official `freqtradeorg/freqtrade` image).
- OKX spot only (`okx` / `myokx`), USDT stake currency, EUR-denominated
  budget options converted at startup.
- `ReboundStrategy` (1h dip-rebound, weighted confirmation scoring,
  stoploss-on-exchange, protections) + unit tests.
- Ingress control panel, optional FreqUI LAN port.
- HA notifications via webhook → Supervisor relay.
- Forced dry-run after every add-on update.
- Notifications also cover Freqtrade warnings and exceptions, so a crashed
  bot is never silent.
- EUR/USDT rate lookup retries both sources for ~3 minutes, so starting
  before the host's network is up is not fatal.
- Test suites: `tests/test_run_sh.sh` (entrypoint safety logic, no container
  needed) and `tests/smoke_container.sh` (image build + Freqtrade config
  validation).
