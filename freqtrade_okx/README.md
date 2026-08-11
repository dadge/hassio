# Home Assistant Add-on: Freqtrade OKX Rebound Bot

[Freqtrade](https://www.freqtrade.io) — the open-source crypto trading bot —
packaged for Home Assistant OS, pre-configured for **OKX spot trading** with:

- 🧠 **`ReboundStrategy`** — dip-rebound entries on the 1h timeframe
  (≥10% drop + weighted reversal confirmation), +5% ROI target, −3% hard
  stop with stoploss-on-exchange, 72h max hold, cooldowns & circuit breakers.
- 🖥️ **Control panel in the HA sidebar** (ingress) + optional full
  **FreqUI** on a LAN port.
- 🔔 **HA notifications** for entries, exits (with profit %), stop-losses,
  circuit breakers, start/stop and mode changes.
- 💶 Budgets configured **in EUR**, converted to USDT stakes at startup using
  the live OKX EUR/USDT rate.
- 🛡️ **Safe by default**: dry-run after install *and* after every update;
  live trading needs an explicit double confirmation.

> ⚠️ **No win rate is guaranteed and the entire allocated budget can be
> lost.** Read the Documentation tab — especially *Security* and the
> *dry-run → backtest → live* workflow — before configuring anything.

Architectures: `amd64`, `aarch64`, `armv7`. The image is built locally on
first install (based on the official `freqtradeorg/freqtrade` image), which
takes a few minutes.

Full documentation: see the **Documentation** tab of this add-on.
