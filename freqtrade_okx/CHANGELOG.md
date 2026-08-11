# Changelog

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
