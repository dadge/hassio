# Changelog

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
