# Changelog

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
