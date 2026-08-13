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
| [binance-bot-dashboard](./binance-bot-dashboard/) | Dashboard for the Binance bot. |
| [shairport_sync](./shairport_sync/) | AirPlay audio receiver. |

> ⚠️ **`freqtrade_okx` trades cryptocurrency.** No win rate is guaranteed and
> the entire budget you allocate to it can be lost. It starts in dry-run mode
> and going live requires a deliberate double confirmation — read its
> Documentation tab, especially *Security* and the *dry-run → backtest → live*
> workflow, before configuring anything.

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
