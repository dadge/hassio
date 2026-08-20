# Hass.io addons repository
Hass.io addons from Dadge

## Installation

In Home Assistant: **Settings → Add-ons → Add-on Store**, then the ⋮ menu
(top right) → **Repositories**, and add:

```
https://github.com/dadge/hassio
```

## Add-ons

| Add-on | Description |
| ------ | ----------- |
| [Freqtrade OKX Rebound Bot](./freqtrade_okx/) | [Freqtrade](https://www.freqtrade.io) `2026.7` for OKX **spot** trading with a dip-rebound strategy, an ingress control panel, optional FreqUI, and Home Assistant notifications. Dry-run by default. |
| [OKX Volatility Harvester](./harvest_okx/) | Constant-weight rebalancing across the most volatile liquid OKX **spot** pairs. Earns the volatility harvest (`sigma²/8`) rather than forecasting price, with an ingress control panel and Home Assistant notifications. Paper trading by default. |
| [binance-bot-dashboard](./binance-bot-dashboard/) | Dashboard for the Binance bot. |
| [shairport_sync](./shairport_sync/) | AirPlay audio receiver. |
| [Jow MCP](./jow_mcp/) | Search and read [Jow](https://jow.fr) recipes over MCP so Claude can find and discuss them. Recipe search, detail, featured and ingredient lookup work with no login. |

The two trading add-ons take opposite approaches. `freqtrade_okx` predicts — it
looks for dips likely to rebound. `harvest_okx` predicts nothing: it holds a
fixed basket at fixed weights and profits from trading back to those weights as
prices move.

> ⚠️ **`freqtrade_okx` and `harvest_okx` trade cryptocurrency.** No win rate is
> guaranteed and the entire budget you allocate to them can be lost. Both start
> in dry-run/paper mode and going live requires a deliberate double
> confirmation — read the relevant Documentation tab, especially *Security* and
> the *dry-run → backtest → live* workflow, before configuring anything.
>
> `harvest_okx` earns a **relative** edge: on the data it was designed against
> it turned −14.0% into −10.6% in a falling market. It beats holding the same
> assets; it does not make a falling market profitable.

## Tests

`freqtrade_okx` ships its own test suites:

```bash
freqtrade_okx/tests/test_run_sh.sh
```

Runs the entrypoint's safety logic (live-mode gating, forced dry-run after an
update, credential validation, EUR→USDT conversion and its refusal to guess a
rate, budget warnings, user-file preservation) against a sandboxed copy of
`run.sh`. Needs only `bash` + `jq` — no container, no network, no API keys.
This suite plus the Home Assistant add-on linter and ShellCheck run in CI.

```bash
freqtrade_okx/tests/smoke_container.sh
```

Builds the add-on image, runs the real entrypoint inside it, and has Freqtrade
validate the generated configuration. Needs Docker and unfiltered internet
access.

```bash
freqtrade_okx/tests/test_ft_helpers.sh
python3 freqtrade_okx/tests/test_control_server.py
```

Covers the panel's backtest control endpoint: input whitelisting, one job at a
time, and result parsing. Three of its cases need POSIX process groups and are
skipped (visibly) on Windows.

The strategy's own unit tests need Freqtrade and TA-Lib, so they run inside the
add-on container via `ft-test-strategy` (see the add-on documentation).

`harvest_okx` ships its own suites too:

```bash
python3 harvest_okx/tests/test_bot.py
python3 harvest_okx/tests/test_addon.py
```

The first exercises the rebalancing engine against a stubbed exchange:
volatility-based selection (and its refusal to rank on past returns), weights
landing on target, value conserved to the cent, no-trade band behaviour, the
live deployment cap, state surviving a restart, and an end-to-end run showing
rebalancing beat holding the same basket. The second is static checks on the
add-on — options/schema/translations agreement, the entrypoint's safety gates,
ingress-relative API calls in the panel, valid icon/logo, and that `run.sh`
passes every config key the bot actually reads. Needs `pyyaml` and `ccxt`
(imported, never called); no network, no API keys.

```bash
harvest_okx/tests/smoke_container.sh
```

Builds the image and runs the real entrypoint inside it against a fake `/data`:
live mode refused without confirmation and without credentials, forced dry-run
after a version bump, no credential value reaching the log, and the panel
answering only the Home Assistant ingress gateway. Needs Docker; it never
places an order.
