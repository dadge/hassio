# Home Assistant Add-on: OKX Volatility Harvester

A rebalancing bot for **OKX spot** that earns the *volatility harvest* instead
of trying to predict prices:

- 📐 **Constant-weight rebalancing** — holds the N most volatile liquid pairs at
  equal weight plus a cash buffer, and trades them back to target whenever one
  drifts outside a band. The return comes from `sigma²/8`, not from forecasting.
- 🎯 **Selects on volatility, never on past returns** — volatility persists
  between periods (rank correlation +0.88 on the test data), past returns do
  not (+0.16).
- 🖥️ **Control panel in the HA sidebar** (ingress) — equity curve, per-leg
  drift bars, pause/resume, force rebalance, reset paper book.
- 🔔 **HA notifications** on rebalances, mode changes and errors.
- 🛡️ **Safe by default** — paper trading after install *and* after every
  update; live needs an explicit double confirmation plus a hard USDT cap on
  money at risk.

> ⚠️ The harvest is **relative**: on the test data it turned −14.0% into −10.6%
> in a falling market and +13.5% into +24.2% in a rising one. It beats holding
> the same assets; it does **not** make a falling market profitable, and the
> deployed amount can be lost. Read the Documentation tab first.

Architectures: `amd64`, `aarch64` (`armv7` was dropped by Home Assistant 2025.12). The image is built locally on first
install (based on `python:3.12-slim`; the only dependency is `ccxt`).

Full documentation, including the maths and how to choose exposure and band:
see the **Documentation** tab of this add-on.
