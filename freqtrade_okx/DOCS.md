# Freqtrade OKX Rebound Bot

> ## ⚠️ Disclaimer — read this first
>
> This add-on runs an automated trading bot against your OKX account.
> **No win rate is guaranteed. Backtest results do not predict future
> performance. The entire budget you allocate to the bot can be lost.**
> Nothing here is financial advice. Use dry-run mode and backtesting first,
> allocate only money you can afford to lose, and monitor the bot regularly.

## What this add-on is

It packages the open-source [Freqtrade](https://www.freqtrade.io) bot
(pinned monthly stable release, see `build.yaml`) configured for **OKX spot
trading** (no margin, no futures, no leverage) with:

- a custom dip-rebound strategy (`ReboundStrategy`, 1h timeframe),
- a control panel in the HA sidebar (ingress) + optional full FreqUI on a LAN port,
- Home Assistant notifications for every trade event,
- persistent state in `/data/user_data` (survives restarts and updates).

Exchange connectivity, order handling, backtesting etc. are stock Freqtrade —
this add-on only packages and configures it.

---

## 1. Security — before you create API keys

Create a **dedicated OKX API key** for this bot (OKX → Profile → API keys):

1. Permissions: **Read + Trade only. Never enable Withdraw.**
2. Set an **IP allowlist** containing only your Home Assistant public IP.
   A leaked key is then useless from anywhere else.
3. Use a strong, unique **passphrase** (OKX requires one per key).
4. If you registered on `my.okx.com` (OKX EAA entity, common for EU users),
   set the `okx_environment` option to `myokx` — otherwise you will see
   `OKX Error 50119: API key doesn't exist`.

The credentials are stored by the Supervisor in the add-on options
(`password`-type fields), injected into the Freqtrade config at startup, and
never written to the logs. The generated config file (`/data/config.json`)
is `chmod 600` inside the container.

Also set your own `api_password` (FreqUI/REST login). If you leave it empty,
a random password is generated once and stored in
`/data/.addon/api_password` (readable via
`docker exec addon_..._freqtrade_okx cat /data/.addon/api_password`).

---

## 2. Options reference

| Option | Default | Description |
| --- | --- | --- |
| `mode` | `dry-run` | `dry-run` = simulated trading, `live` = real money. Changing it restarts the add-on with the new mode (see §4). |
| `i_understand_live_trading` | `false` | Second confirmation required for live mode. The add-on refuses to start live without it. |
| `okx_environment` | `okx` | `okx` for okx.com accounts, `myokx` for my.okx.com (EAA) accounts. |
| `strategy` | `ReboundStrategy` | Which strategy to trade — see §2.1. Any name in `/data/user_data/strategies/`, without `.py`. |
| `okx_api_key/secret/passphrase` | empty | The OKX API credentials. Optional in dry-run (public data only), required for live. |
| `stake_currency` | `USDT` | `USDT` (deepest books) or `USDC`. See §3.1 — EEA accounts on `my.okx.com` may be unable to trade USDT under MiCA. |
| `stake_amount_eur` | `20` | Stake per trade **in EUR** — converted to the stake currency at startup (see §3). |
| `max_total_exposure_eur` | `100` | Cap on the total capital the bot may use, in EUR → converted (`available_capital`). |
| `max_open_trades` | `3` | Maximum simultaneous positions. |
| `dry_run_wallet` | `1000` | Simulated wallet (stake currency) for dry-run mode. |
| `pairlist_min_volume` | `1000000` | Minimum 24h quote volume for a pair to be tradable. Lower it for USDC. |
| `pairlist_max_spread_percent` | `0.5` | Maximum bid/ask spread. A wide spread is paid twice (entry and exit), so it eats the ROI target. USDC books may need 0.8–1.0 to yield a usable pairlist. |
| `api_username` / `api_password` | `freqtrade` / auto | Login for FreqUI and the REST API. |
| `notifications_enabled` | `true` | Master switch for HA notifications. |
| `notify_service` | `notify.mobile_app_op15` | Any HA notify service (`notify.<something>`). |
| `cors_origins` | `[]` | Extra allowed origins for the REST API (only needed for external FreqUI instances). |
| `log_level` | `info` | `debug` adds verbose Freqtrade logging. |

### 2.1 Strategies

Three sources, all selectable by name with the `strategy` option:

| | What it is |
| --- | --- |
| `ReboundStrategy` | Bundled. 1h dip-rebound, the add-on's default. |
| `MeanRevert15m` | Bundled. 15m mean reversion, built around the cost of thin USDC books (see its docstring for the risk arithmetic). |
| 68 community examples | Copied from [freqtrade/freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies), **GPL-3.0** (the add-on itself is MIT). Licence and notes ship as `community-LICENSE` / `community-README.md` in your strategies folder. |
| your own | Drop a `.py` into `/data/user_data/strategies/` and name its class. |

An unknown name is refused at startup with the list of what is available.

**The community examples are unreviewed.** Upstream publishes them as
teaching material, not as strategies to trade. Two groups are actively unsafe
here and the add-on guards them rather than trusting you to remember:

- **`futures/` (7 strategies)** short and use leverage. This add-on is
  spot-only, so selecting one fails at startup.
- **`lookahead_bias/` (4 strategies)** read future candles *on purpose* —
  upstream ships them so you can practise spotting the mistake. They backtest
  brilliantly and lose money live. Live mode with one is refused; dry-run
  warns.

Treat a good backtest from any of them with suspicion until you have
out-of-sample numbers: on real OKX data over 181 days, both bundled strategies
lost money, and an 8-trade sample showed a 62% win rate that a 163-trade
sample revealed as 39%.

## 3. Why EUR options on a USDT/USDC bot?

OKX has almost no EUR spot pairs, so the bot trades **USDT- or USDC-quoted**
pairs (see §3.1). To let you think in EUR, the two budget options are given in
EUR and converted **once per add-on start**:

1. The live rate is read from OKX's public ticker API. Note the direction:
   OKX lists **`USDT-EUR`** (EUR per USDT, ~0.87) — there is no `EUR-USDT` or
   `EUR-USDC` instrument — so the add-on inverts that quote to get EUR→USDT.
   Fallback: the ECB reference rate via `api.frankfurter.dev`, treating
   USDT ≈ USD. The add-on retries both sources for up to 3 minutes, so
   starting before the host's network is up (e.g. after a power cut) is not
   a problem;
2. `stake_amount_eur × rate → stake_amount`,
   `max_total_exposure_eur × rate → available_capital`;
3. The conversion is logged at startup and included in the start notification.

If no rate can be fetched at all, the add-on **refuses to start** rather than
guessing (without OKX connectivity the bot could not trade anyway). Note the
converted amounts stay fixed until the next restart; a moving EUR/USDT rate
does not resize open positions.

### 3.1 USDT or USDC? (important for EU accounts)

`stake_currency` defaults to **USDT**, which has by far the deepest books on
OKX. But if you registered on **`my.okx.com`** (the EEA entity — the same
accounts that need `okx_environment: myokx`), MiCA rules restrict USDT
trading, and **USDC** is the practical quote currency.

Note that this is *not* visible in dry-run: `my.okx.com`'s public API serves
USDT tickers to everyone, so the pairlist fills with `XXX/USDT` pairs and
everything looks healthy. The restriction only bites when a real order is
placed. **Check on the exchange whether your account can trade USDT spot
pairs before enabling live mode**; if it cannot, set `stake_currency: USDC`.

When switching to USDC, also lower `pairlist_min_volume`: USDC books on OKX
are roughly an order of magnitude thinner than USDT ones (BTC/USDC traded
~348 BTC/24h vs ~5551 BTC/24h for BTC/USDT at the time of writing), so far
fewer pairs clear a 1,000,000 threshold.

The EUR conversion follows the choice automatically: it reads `USDT-EUR` or
`USDC-EUR` and inverts it.

## 4. Paper / live switching

- Fresh installs run in **dry-run**. Dry-run uses a simulated wallet
  (`dry_run_wallet_usdt`) and places no real orders.
- Freqtrade's `dry_run` flag requires a restart to change; the Supervisor
  restarts the add-on automatically whenever you save its configuration, so
  **toggling the `mode` option is the switch**. On every start and every mode
  change you get an HA notification stating the active mode, and both the
  ingress panel and FreqUI display it.
- Going **live** requires *two* deliberate steps: `mode: live` **and**
  `i_understand_live_trading: true`. Without the second flag the add-on stops
  with a clear log message.
- **After every add-on update the bot is forced back to dry-run** (the stored
  options are reset via the Supervisor API and a notification is sent).
  Re-enable live mode explicitly after reviewing the changelog. If the
  automatic option reset ever fails, the add-on keeps forcing dry-run until
  you re-save the configuration (set `mode: dry-run`, save, then back to
  `live` if desired).

To automate restarts/mode visibility from HA, the usual services work:
`hassio.addon_restart`, `hassio.addon_stop` with
`addon: <your_repo_hash>_freqtrade_okx`.

## 5. Web UI

### Sidebar panel (ingress)

The **Freqtrade** entry in the HA sidebar opens a lightweight control panel:
mode banner (dry-run/live), bot state, open trades with live profit, closed
profit, win rate, and Start / Stop / Reload buttons. It needs no login —
access is protected by Home Assistant's own authentication, and an internal
nginx proxy injects the API credentials server-side. It only ever listens to
the Supervisor's ingress gateway (`172.30.32.2`).

### Full FreqUI (optional, LAN port)

FreqUI itself **cannot run behind HA ingress**, and this is a hard technical
limitation, not a configuration issue:

- FreqUI is built with Vite's absolute base path (`/`), so its assets resolve
  against the HA host root instead of the ingress prefix;
- its vue-router uses HTML5 history mode with base `/` — the first in-app
  navigation rewrites the URL outside `/api/hassio_ingress/<token>/…`,
  breaking the session;
- Freqtrade's API server has no path-prefix/`root_path` support to compensate.

Hence the documented fallback: to use full FreqUI, open the add-on's
**Configuration → Network** section, map container port `8080` to a host port
(e.g. `8080`), then browse to `http://<ha-host>:8080` and log in with
`api_username` / `api_password`. Keep this on your LAN — do **not** port
forward it to the internet.

## 6. Home Assistant notifications

Freqtrade's webhook feature posts every event to a local relay inside the
add-on, which forwards it to the HA Core API (`SUPERVISOR_TOKEN` auth) as a
call to your `notify_service`. You get notifications for:

- trade entry / entry filled / entry cancelled,
- exit / exit filled (with profit % and exit reason — stop-loss hits show
  `stop_loss`),
- protections / circuit breakers triggering (per-pair and global),
- bot start / stop / mode change (each stating the active mode `[DRY-RUN]` /
  `[LIVE]`),
- Freqtrade warnings and **exceptions** — if the bot crashes or stops trading
  you are told, instead of silently not trading.

## 7. Dashboard sensors (REST)

The Freqtrade REST API accepts HTTP Basic auth, so plain HA REST sensors
work. Map port `8080` first (§5), then add to `configuration.yaml` (with
`freqtrade_api_password` in `secrets.yaml`):

```yaml
rest:
  - resource: http://127.0.0.1:8080/api/v1/show_config
    username: freqtrade
    password: !secret freqtrade_api_password
    authentication: basic
    scan_interval: 60
    sensor:
      - name: "Freqtrade state"
        value_template: "{{ value_json.state }}"
      - name: "Freqtrade mode"
        value_template: "{{ 'dry-run' if value_json.dry_run else 'LIVE' }}"
  - resource: http://127.0.0.1:8080/api/v1/count
    username: freqtrade
    password: !secret freqtrade_api_password
    authentication: basic
    scan_interval: 60
    sensor:
      - name: "Freqtrade open trades"
        value_template: "{{ value_json.current }}"
  - resource: http://127.0.0.1:8080/api/v1/profit
    username: freqtrade
    password: !secret freqtrade_api_password
    authentication: basic
    scan_interval: 120
    sensor:
      - name: "Freqtrade total profit"
        unit_of_measurement: "USDT"
        value_template: "{{ value_json.profit_closed_coin | round(2) }}"
      - name: "Freqtrade profit percent"
        unit_of_measurement: "%"
        value_template: "{{ value_json.profit_closed_percent_sum | round(2) }}"
```

(`127.0.0.1` works because HA Core runs on the host network and the mapped
add-on port is bound on the host; adjust host/port if you mapped differently.)

## 8. Backtesting & validation — REQUIRED before live

**Acceptance step: do not enable live mode before you have run at least a
6-month backtest and reviewed win rate, profit factor and max drawdown.**

### From the sidebar panel (no shell needed)

The **Backtesting** card in the ingress panel runs the same helpers for you:
pick a quote currency, press **Download data**, then **Run backtest**. Progress
and the tail of the log are shown live, and the finished run is summarised as
trades / win rate / profit factor / total profit / max drawdown.

The card talks to a small control endpoint inside the add-on
(`127.0.0.1:8125`, reachable only through ingress, which Home Assistant
authenticates). It exists because Freqtrade's own backtest API is only served
in *webserver* mode, which the trading bot is not running in. If the endpoint
fails to start, the card reports it and trading carries on unaffected.

### From a container shell

Open a shell in the add-on container. The **official Terminal & SSH add-on
cannot do this** — it has no Docker access. Use the community *Advanced SSH &
Web Terminal* add-on with Protection mode disabled, then `docker exec -it
addon_<hash>_freqtrade_okx bash` (find the exact name with `docker ps | grep
freqtrade`). Three helpers are bundled:

```bash
# 1. Download ~8 months of OKX 1h data for the current volume pairlist
#    (OKX serves only 100 candles per request, so this takes a while):
ft-download-data 240

# 2. Backtest the last 6 months (the acceptance step):
ft-backtest

# 3. Optional: optimize the strategy's buy parameters:
ft-hyperopt 100
```

Both helpers take `--stake-currency USDT|USDC` to backtest against a quote
currency other than the one the bot runs with — pass the same value to both:

```bash
ft-download-data 240 --stake-currency USDT
ft-backtest --stake-currency USDT
```

This matters on EEA accounts: USDC pairs on OKX are young and thin, so a
USDC backtest often has too little history and too few pairs to mean anything,
while the *strategy* is equally valid on USDT candles — `BTC/USDT` and
`BTC/USDC` are the same asset.

What the helpers do, if you prefer the raw commands:

`/data/config_backtest.json` is generated at every add-on start (same
settings as the live config, but always dry-run, no webhook, no API port).
Backtesting and hyperopt cannot use a dynamic `VolumePairList`, so
`ft-download-data` first resolves the current pairlist and writes
`/data/config_backtest_static.json` with a `StaticPairList` — run it once
before backtesting:

```bash
# 1. Resolve today's volume pairlist -> static backtest config
freqtrade test-pairlist --config /data/config.json --print-json | tail -n 1 > /tmp/pairs.json
jq --argjson p "$(cat /tmp/pairs.json)" \
   '.exchange.pair_whitelist = $p | .pairlists = [{method: "StaticPairList"}]' \
   /data/config_backtest.json > /data/config_backtest_static.json

# 2. Download the candles (OKX serves 100 candles per call — be patient)
freqtrade download-data --config /data/config_backtest_static.json \
    --userdir /data/user_data --timeframe 1h --days 240

# 3. Backtest six months (adjust the start date to ~6 months ago)
freqtrade backtesting --config /data/config_backtest_static.json \
    --userdir /data/user_data --strategy ReboundStrategy \
    --timeframe 1h --timerange "$(date -d '6 months ago' +%Y%m%d)-" --export trades
```

How to read the results:

- **Win rate** (`Win Draw Loss Win%`): with the default +5% ROI / −3% stop,
  expect the strategy to live or die on this ratio. Below ~55% with these
  targets it likely loses after fees.
- **Profit factor**: gross profit ÷ gross loss. Demand > 1.2 over 6 months.
- **Max drawdown** (`Absolute Drawdown (Account)`): the worst peak-to-valley
  loss. If you would not tolerate that number with real money, do not go live.
- **Exit reason stats**: healthy runs show most profit from `roi` exits and
  contained `stop_loss` losses; many `max_hold_time` exits mean the dip
  filter is buying falling knives.

Strategy unit tests (indicator/scoring logic on synthetic dataframes):

```bash
ft-test-strategy
```

The add-on's own start-up logic (live-mode gating, forced dry-run after an
update, EUR→USDT conversion, option validation) is covered by
`freqtrade_okx/tests/test_run_sh.sh` in the add-on repository — run it on any machine with
`bash` and `jq`, no container or network needed.

## 9. Recommended workflow

1. Install → leave `mode: dry-run` → watch the panel/notifications for a few
   days.
2. Run the backtest acceptance step (§8) and the unit tests.
3. Only then: create the restricted OKX API key (§1), set a small
   `stake_amount_eur` and `max_total_exposure_eur`, set `mode: live` **and**
   `i_understand_live_trading: true`, save.
4. Re-check §1: trade-only key, IP allowlist, no withdrawal permission.

## 10. Data layout & persistence

Everything lives in the add-on's `/data` volume and survives
updates/restarts:

```
/data/user_data/            Freqtrade userdir
  ├── strategies/ReboundStrategy.py   (updated on add-on update unless you edited it)
  ├── data/okx/                       downloaded OHLCV
  ├── backtest_results/               backtest exports
  ├── tests/                          strategy unit tests
  ├── tradesv3.dryrun.sqlite          dry-run trade DB
  └── tradesv3.sqlite                 live trade DB (separate!)
/data/config.json           generated Freqtrade config (600)
/data/.addon/               generated secrets, version/mode markers
```

If you modify `ReboundStrategy.py`, your version is kept on updates (a log
line warns that the bundled version differs). Delete the file and restart to
get the bundled version back.

## 11. Troubleshooting

- **`OKX Error 50119`** → wrong `okx_environment`; switch `okx` ↔ `myokx`.
- **`mode=live requires ...` in the log** → set the missing credential or
  `i_understand_live_trading: true`.
- **No notifications** → check `notify_service` exists (Developer Tools →
  Actions), and `notifications_enabled: true`.
- **Panel says BOT UNREACHABLE** → the bot is still starting (pairlist
  warm-up takes ~30 s) or crashed — check the add-on Log tab.
- **Backtest says "No data found."** → run `ft-download-data` first, with the
  same `--stake-currency` you intend to backtest.
- **Whitelist has only a handful of pairs** → almost always the spread filter
  on thin books. Raise `pairlist_max_spread_percent` (0.8–1.0) and/or lower
  `pairlist_min_volume`, and see §3.1 — USDC is the usual cause.
- **The panel's Backtesting card says the control endpoint is unreachable** →
  check the add-on log for "Backtest control endpoint"; trading is unaffected,
  and the `ft-*` helpers still work from a container shell.
- **Entries rejected on OKX** → stake below the pair's minimum order size;
  raise `stake_amount_eur`.
