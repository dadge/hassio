#!/usr/bin/env bash
# ==============================================================================
# Freqtrade OKX Rebound Bot — Home Assistant add-on entrypoint
#
# Responsibilities:
#   1. Read & validate add-on options (/data/options.json)
#   2. Enforce the paper/live safety rules (dry-run by default, forced dry-run
#      after every add-on update, explicit confirmation for live)
#   3. Convert EUR budget options to the stake currency (USDT or USDC)
#   4. Generate the Freqtrade configuration (secrets never touch the logs)
#   5. Start nginx (ingress control panel + HA notification relay)
#   6. Send start/mode-change notifications to Home Assistant
#   7. exec freqtrade
# ==============================================================================
set -Eeuo pipefail

OPTIONS_FILE=/data/options.json
STATE_DIR=/data/.addon
USER_DATA=/data/user_data
FT_CONFIG=/data/config.json
BT_CONFIG=/data/config_backtest.json
NGINX_CONF=/etc/nginx/nginx.conf

# ------------------------------------------------------------------ logging --
# Log to stderr, never stdout: helper functions below run inside command
# substitutions ($(...)), where a log line on stdout would be captured as part
# of the return value. The add-on Log tab shows both streams.
log()   { printf '%s [%-5s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "${*:2}" >&2; }
info()  { log INFO "$@"; }
warn()  { log WARN "$@"; }
error() { log ERROR "$@"; }
fatal() { log FATAL "$@"; log FATAL "Add-on stopped. Fix the configuration and restart."; exit 1; }

[[ -f "$OPTIONS_FILE" ]] || fatal "Options file $OPTIONS_FILE not found (is this running as an HA add-on?)"

opt() { jq -r --arg k "$1" 'if .[$k] == null then "" else (.[$k] | tostring) end' "$OPTIONS_FILE"; }

# ------------------------------------------------------------- read options --
MODE="$(opt mode)"
I_UNDERSTAND="$(opt i_understand_live_trading)"
OKX_ENV="$(opt okx_environment)"
OKX_KEY="$(opt okx_api_key)"
OKX_SECRET="$(opt okx_api_secret)"
OKX_PASSPHRASE="$(opt okx_api_passphrase)"
STRATEGY="$(opt strategy)"
STAKE_CCY="$(opt stake_currency)"
STAKE_EUR="$(opt stake_amount_eur)"
EXPOSURE_EUR="$(opt max_total_exposure_eur)"
MAX_OPEN_TRADES="$(opt max_open_trades)"
DRY_WALLET="$(opt dry_run_wallet)"
MIN_VOLUME="$(opt pairlist_min_volume)"
MAX_SPREAD_PCT="$(opt pairlist_max_spread_percent)"
API_USERNAME="$(opt api_username)"
API_PASSWORD="$(opt api_password)"
NOTIFY_ENABLED="$(opt notifications_enabled)"
NOTIFY_SERVICE="$(opt notify_service)"
LOG_LEVEL="$(opt log_level)"

mkdir -p "$STATE_DIR" "$USER_DATA"
chmod 700 "$STATE_DIR"

# -------------------------------------------------------- HA notifications --
# notify.mobile_app_op15 -> core API path services/notify/mobile_app_op15
NOTIFY_DOMAIN="${NOTIFY_SERVICE%%.*}"
NOTIFY_NAME="${NOTIFY_SERVICE#*.}"
ha_notify() {  # $1 = title, $2 = message
    [[ "$NOTIFY_ENABLED" == "true" ]] || return 0
    local payload
    payload="$(jq -cn --arg t "$1" --arg m "$2" '{title: $t, message: $m}')"
    if ! curl -fsS -m 10 -o /dev/null -X POST \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN:-}" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "http://supervisor/core/api/services/${NOTIFY_DOMAIN}/${NOTIFY_NAME}"; then
        warn "Could not send HA notification via '${NOTIFY_SERVICE}' — check the notify_service option"
    fi
}

# ------------------------------------------------------- basic validation ---
[[ "$MODE" == "dry-run" || "$MODE" == "live" ]] || fatal "Invalid mode '$MODE' (must be dry-run or live)"
[[ "$NOTIFY_SERVICE" == *.* ]] || fatal "notify_service must look like 'notify.mobile_app_xxx' (got '$NOTIFY_SERVICE')"
[[ -n "$API_USERNAME" ]] || fatal "api_username must not be empty"
# USDT has by far the deepest books on OKX, but its EEA entity (myokx)
# restricts it under MiCA, where USDC is the practical quote currency.
[[ "$STAKE_CCY" == "USDT" || "$STAKE_CCY" == "USDC" ]] \
    || fatal "Invalid stake_currency '$STAKE_CCY' (must be USDT or USDC)"
awk "BEGIN{exit !($MAX_SPREAD_PCT > 0 && $MAX_SPREAD_PCT <= 5)}" \
    || fatal "pairlist_max_spread_percent must be between 0 and 5 (got '$MAX_SPREAD_PCT')"

# Check the bot itself is runnable BEFORE the exchange-rate lookup, so a broken
# image fails in a second with a clear message instead of after 3 minutes of
# retries followed by a Python traceback. The base image installs Freqtrade
# into ftuser's user site (editable install); PYTHONUSERBASE in the Dockerfile
# makes that importable as root.
FT_VERSION="$(freqtrade --version 2>&1 | tail -n 1)" || fatal \
    "Freqtrade is installed but not runnable: ${FT_VERSION:-no output}. \
This is an add-on packaging fault, not a configuration error — please report it."
info "Freqtrade: ${FT_VERSION}"

# ---------------------------------------- forced dry-run after add-on update --
# Requirement: after EVERY add-on update the bot must come back up in dry-run.
# We detect updates via the image build version and flip the stored option back
# to dry-run through the Supervisor API. If that API call fails, we refuse to
# honour mode=live until the user re-saves the configuration (dry-run -> live).
CURRENT_VERSION="${ADDON_VERSION:-unknown}"
PREV_VERSION="$(cat "$STATE_DIR/version" 2>/dev/null || echo "")"
FORCED_DRY_RUN="no"
if [[ "$MODE" == "live" && -n "$PREV_VERSION" && "$PREV_VERSION" != "$CURRENT_VERSION" ]]; then
    warn "Add-on updated ($PREV_VERSION -> $CURRENT_VERSION) while mode=live."
    warn "SAFETY: forcing DRY-RUN. Re-enable live mode explicitly in the add-on configuration."
    MODE="dry-run"
    FORCED_DRY_RUN="yes"
    new_opts="$(jq -c '{options: (. + {mode: "dry-run", i_understand_live_trading: false})}' "$OPTIONS_FILE")"
    if curl -fsS -m 10 -o /dev/null -X POST \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN:-}" \
        -H "Content-Type: application/json" \
        -d "$new_opts" "http://supervisor/addons/self/options"; then
        info "Stored add-on options were reset to dry-run."
        echo "$CURRENT_VERSION" > "$STATE_DIR/version"
    else
        warn "Could not persist the option reset via the Supervisor API."
        warn "The bot will keep starting in DRY-RUN until you re-save the configuration:"
        warn "  set mode=dry-run, save, then set mode=live again if you really want live trading."
        # deliberately NOT updating the version marker so this branch re-runs
    fi
    ha_notify "Freqtrade — forced dry-run" \
        "The add-on was updated to $CURRENT_VERSION. For safety the bot restarted in DRY-RUN mode. Re-enable live mode in the add-on configuration if desired."
else
    echo "$CURRENT_VERSION" > "$STATE_DIR/version"
fi

# --------------------------------------------------------- live-mode gating --
if [[ "$MODE" == "live" ]]; then
    if [[ "$I_UNDERSTAND" != "true" ]]; then
        fatal "mode=live requires the option 'i_understand_live_trading: true'. \
This is a deliberate second confirmation: live mode trades REAL money on your OKX account."
    fi
    [[ -n "$OKX_KEY" ]]        || fatal "mode=live requires okx_api_key"
    [[ -n "$OKX_SECRET" ]]     || fatal "mode=live requires okx_api_secret"
    [[ -n "$OKX_PASSPHRASE" ]] || fatal "mode=live requires okx_api_passphrase (OKX API keys always have a passphrase)"
else
    if [[ -z "$OKX_KEY" || -z "$OKX_SECRET" || -z "$OKX_PASSPHRASE" ]]; then
        info "No/partial OKX credentials configured — fine for dry-run (public market data only)."
    fi
fi
DRY_RUN_JSON=$([[ "$MODE" == "live" ]] && echo "false" || echo "true")
MODE_TAG=$([[ "$MODE" == "live" ]] && echo "[LIVE]" || echo "[DRY-RUN]")

# ------------------------------------------------- secrets & generated keys --
# API password: use the option if set, otherwise generate one once and persist.
# Credential VALUES are never logged.
if [[ -z "$API_PASSWORD" ]]; then
    if [[ -f "$STATE_DIR/api_password" ]]; then
        API_PASSWORD="$(cat "$STATE_DIR/api_password")"
    else
        API_PASSWORD="$(head -c 48 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)"
        (umask 077 && printf '%s' "$API_PASSWORD" > "$STATE_DIR/api_password")
        info "Generated a random FreqUI/API password (stored in /data/.addon/api_password)."
        info "Set your own via the 'api_password' option, or read the generated one from that file."
    fi
fi
gen_secret() {  # $1 = state file name
    local f="$STATE_DIR/$1"
    if [[ ! -f "$f" ]]; then
        (umask 077 && head -c 48 /dev/urandom | base64 | tr -d '=+/' | head -c 40 > "$f")
    fi
    cat "$f"
}
JWT_SECRET="$(gen_secret jwt_secret)"
WS_TOKEN="$(gen_secret ws_token)"

# ----------------------------------------------------- EUR -> stake amounts --
# The bot quotes in USDT or USDC (OKX has almost no EUR spot pairs), so the EUR
# options are converted at startup from a live rate. If no rate can be fetched
# the add-on refuses to start: without OKX connectivity the bot could not trade
# anyway, and guessing an FX rate would silently change stake sizes.
#
# Rate sources, in order:
#   1. OKX's own <STAKE>-EUR spot ticker. NOTE the direction: OKX lists
#      USDT-EUR / USDC-EUR (EUR per stablecoin, ~0.87) and NOT the reverse —
#      neither EUR-USDT nor EUR-USDC exists, and asking for one returns error
#      51001 with empty data. The EUR->stake rate is the INVERSE of that quote.
#   2. ECB reference rate EUR->USD via frankfurter.dev, treating the
#      stablecoin as USD.
#      (api.frankfurter.app now 301-redirects here, hence -L on every call.)
#
# The add-on can start before the host's network is up (e.g. after a power
# cut), so each round tries both sources and the loop keeps retrying for
# ~3 minutes before giving up.
rate_is_sane() {  # a rate outside this band means a broken response
    [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]] && awk "BEGIN{exit !($1 > 0.3 && $1 < 3)}"
}
fetch_eur_stake_rate() {
    local quote rate attempt
    # myokx accounts read the same public ticker host; only trading is entity-bound.
    for attempt in $(seq 1 12); do
        # 1. OKX <STAKE>-EUR, inverted. Same venue the bot trades on.
        quote="$(curl -fsSL -m 10 "https://www.okx.com/api/v5/market/ticker?instId=${STAKE_CCY}-EUR" 2>/dev/null \
                 | jq -r '.data[0].last // empty' 2>/dev/null || true)"
        if rate_is_sane "$quote"; then
            rate="$(awk "BEGIN{printf \"%.6f\", 1 / $quote}")"
            if rate_is_sane "$rate"; then
                echo "$rate"; return 0
            fi
        fi
        if [[ -n "$quote" ]]; then
            warn "OKX ${STAKE_CCY}-EUR ticker returned an unusable price ('$quote')."
        else
            warn "OKX ${STAKE_CCY}-EUR ticker unreachable or empty."
        fi

        # 2. ECB reference rate.
        quote="$(curl -fsSL -m 10 'https://api.frankfurter.dev/v1/latest?from=EUR&to=USD' 2>/dev/null \
                 | jq -r '.rates.USD // empty' 2>/dev/null || true)"
        if rate_is_sane "$quote"; then
            warn "Falling back to the ECB EUR/USD reference rate (${STAKE_CCY} treated as USD)."
            echo "$quote"; return 0
        fi
        warn "ECB reference rate (api.frankfurter.dev) unreachable or empty as well."

        [[ "$attempt" -lt 12 ]] || break
        warn "No EUR/${STAKE_CCY} rate yet (attempt $attempt/12) — waiting 15s for network connectivity..."
        sleep 15
    done
    return 1
}
EUR_STAKE_RATE="$(fetch_eur_stake_rate)" || fatal "Could not determine the EUR/${STAKE_CCY} exchange rate from any source after 3 minutes. \
Check the internet connection of your Home Assistant host (the bot needs to reach www.okx.com to trade anyway)."

STAKE_AMT="$(awk "BEGIN{printf \"%.2f\", $STAKE_EUR * $EUR_STAKE_RATE}")"
EXPOSURE_AMT="$(awk "BEGIN{printf \"%.2f\", $EXPOSURE_EUR * $EUR_STAKE_RATE}")"
info "EUR/${STAKE_CCY} rate: $EUR_STAKE_RATE — stake: ${STAKE_EUR} EUR ≈ ${STAKE_AMT} ${STAKE_CCY}, max exposure: ${EXPOSURE_EUR} EUR ≈ ${EXPOSURE_AMT} ${STAKE_CCY}"

if awk "BEGIN{exit !($STAKE_AMT < 5)}"; then
    warn "Stake of ${STAKE_AMT} ${STAKE_CCY} is very small — OKX minimum order sizes (~1-5) may reject entries on some pairs."
fi
if awk "BEGIN{exit !($STAKE_AMT * $MAX_OPEN_TRADES > $EXPOSURE_AMT)}"; then
    warn "stake_amount_eur × max_open_trades exceeds max_total_exposure_eur."
    warn "Freqtrade will stop opening new trades once the exposure cap (available_capital=${EXPOSURE_AMT} ${STAKE_CCY}) is reached."
fi
# In dry-run the exposure cap cannot exceed the simulated wallet.
AVAILABLE_CAPITAL="$EXPOSURE_AMT"
if [[ "$MODE" == "dry-run" ]] && awk "BEGIN{exit !($EXPOSURE_AMT > $DRY_WALLET)}"; then
    AVAILABLE_CAPITAL="$DRY_WALLET"
    info "Capping available_capital to the dry-run wallet (${DRY_WALLET} ${STAKE_CCY})."
fi

if awk "BEGIN{exit !($MAX_SPREAD_PCT <= 0.5 && \"$STAKE_CCY\" == \"USDC\")}" 2>/dev/null; then
    warn "stake_currency=USDC with a ${MAX_SPREAD_PCT}% spread cap will leave very few pairs:"
    warn "  USDC books on OKX are much thinner than USDT ones. Raise"
    warn "  pairlist_max_spread_percent (0.8-1.0) if the whitelist is nearly empty."
fi

# ------------------------------------------------------------ user_data dir --
# Everything stateful (strategies, DB, logs, backtest results, downloaded
# data) lives under /data/user_data and therefore survives updates/restarts.
mkdir -p "$USER_DATA/strategies" "$USER_DATA/data" "$USER_DATA/logs" \
         "$USER_DATA/backtest_results" "$USER_DATA/hyperopt_results" \
         "$USER_DATA/notebooks" "$USER_DATA/plot" "$USER_DATA/tests"

# Deploy the bundled strategy & tests, but never clobber user modifications:
# overwrite only when the existing file is identical to a previously shipped
# version (tracked by checksum).
deploy_file() {  # $1 = source, $2 = target, $3 = checksum state file
    local src="$1" dst="$2" state="$STATE_DIR/$3"
    if [[ ! -f "$dst" ]]; then
        cp "$src" "$dst"
    elif [[ -f "$state" ]] && sha256sum -c --status <(printf '%s  %s\n' "$(cat "$state")" "$dst") 2>/dev/null; then
        cp "$src" "$dst"   # untouched previous version -> safe to update
    elif ! cmp -s "$src" "$dst"; then
        warn "Keeping your locally modified $(basename "$dst") (bundled version differs; delete the file to get the update)."
    fi
    sha256sum "$src" | awk '{print $1}' > "$state"
}
deploy_file /defaults/strategies/ReboundStrategy.py "$USER_DATA/strategies/ReboundStrategy.py" strategy.sha
deploy_file /defaults/strategies/MeanRevert15m.py "$USER_DATA/strategies/MeanRevert15m.py" strategy_mr15.sha
deploy_file /defaults/tests/test_rebound_strategy.py "$USER_DATA/tests/test_rebound_strategy.py" test.sha
deploy_file /defaults/tests/test_meanrevert15m.py "$USER_DATA/tests/test_meanrevert15m.py" test_mr15.sha

# The strategy is selectable, including files you drop in yourself. Validate it
# here rather than letting freqtrade fail with a stack trace 30 seconds later.
if [[ ! -f "$USER_DATA/strategies/${STRATEGY}.py" ]]; then
    error "Strategy '${STRATEGY}' not found ($USER_DATA/strategies/${STRATEGY}.py)."
    error "Available strategies:"
    for f in "$USER_DATA"/strategies/*.py; do
        [[ -e "$f" ]] && error "  - $(basename "$f" .py)"
    done
    fatal "Set the 'strategy' option to one of the names above, or copy your own .py into $USER_DATA/strategies/."
fi
info "Strategy: ${STRATEGY}"

# --------------------------------------------------- freqtrade config files --
DB_FILE="tradesv3.sqlite"
[[ "$MODE" == "dry-run" ]] && DB_FILE="tradesv3.dryrun.sqlite"

CORS_JSON="$(jq -c '.cors_origins // []' "$OPTIONS_FILE")"

jq -n \
    --argjson dry_run "$DRY_RUN_JSON" \
    --argjson dry_run_wallet "$DRY_WALLET" \
    --argjson max_open_trades "$MAX_OPEN_TRADES" \
    --argjson stake_amount "$STAKE_AMT" \
    --argjson available_capital "$AVAILABLE_CAPITAL" \
    --argjson min_volume "$MIN_VOLUME" \
    --argjson max_spread "$(awk "BEGIN{printf \"%.6f\", $MAX_SPREAD_PCT / 100}")" \
    --argjson cors "$CORS_JSON" \
    --arg exchange_name "$OKX_ENV" \
    --arg stake_currency "$STAKE_CCY" \
    --arg strategy "$STRATEGY" \
    --arg key "$OKX_KEY" \
    --arg secret "$OKX_SECRET" \
    --arg password "$OKX_PASSPHRASE" \
    --arg api_username "$API_USERNAME" \
    --arg api_password "$API_PASSWORD" \
    --arg jwt_secret "$JWT_SECRET" \
    --arg ws_token "$WS_TOKEN" \
    --arg db_url "sqlite:////data/user_data/$DB_FILE" \
    --arg mode_tag "$MODE_TAG" \
    '{
        bot_name: "freqtrade-okx-rebound",
        strategy: $strategy,
        trading_mode: "spot",
        margin_mode: "",
        max_open_trades: $max_open_trades,
        stake_currency: $stake_currency,
        stake_amount: $stake_amount,
        available_capital: $available_capital,
        fiat_display_currency: "EUR",
        dry_run: $dry_run,
        dry_run_wallet: $dry_run_wallet,
        cancel_open_orders_on_exit: false,
        db_url: $db_url,
        # Belt and braces alongside --userdir on every command line: without
        # this, any freqtrade invocation that forgets the flag resolves
        # user_data against the working directory (/) and aborts.
        user_data_dir: "/data/user_data",
        dataformat_ohlcv: "feather",
        unfilledtimeout: { entry: 10, exit: 10, exit_timeout_count: 0, unit: "minutes" },
        entry_pricing: {
            price_side: "same",
            use_order_book: true,
            order_book_top: 1,
            price_last_balance: 0.0,
            check_depth_of_market: { enabled: false, bids_to_ask_delta: 1 }
        },
        exit_pricing: { price_side: "same", use_order_book: true, order_book_top: 1 },
        exchange: {
            name: $exchange_name,
            key: $key,
            secret: $secret,
            password: $password,
            ccxt_config: {},
            ccxt_async_config: {},
            pair_whitelist: [],
            pair_blacklist: [
                # Stablecoin bases. They cannot drop 10% unless they DEPEG, and a
                # depeg is the one "dip" that must never be bought (see USTC).
                ("(USDT|USDC|TUSD|DAI|FDUSD|USDP|PYUSD|GUSD|EURT|USTC|USDe" +
                 "|BUSD|RLUSD|USDG|USD0|USDS|USDD|LUSD|FRAX)/.*"),
                "(EUR|GBP|AUD|TRY|BRL)/.*",
                ".*(3L|3S|5L|5S)/.*",
                ("[A-Z0-9]+(UP|DOWN|BULL|BEAR)/" + $stake_currency)
            ]
        },
        pairlists: [
            {
                method: "VolumePairList",
                number_assets: 60,
                sort_key: "quoteVolume",
                min_value: $min_volume,
                refresh_period: 1800
            },
            { method: "AgeFilter", min_days_listed: 30 },
            { method: "PriceFilter", low_price_ratio: 0.01 },
            { method: "SpreadFilter", max_spread_ratio: $max_spread }
        ],
        webhook: {
            enabled: true,
            url: "http://127.0.0.1:8124/notify",
            format: "json",
            retries: 3,
            retry_delay: 0.2,
            allow_custom_messages: true,
            entry: {
                title: "Freqtrade — entry",
                message: ("\($mode_tag) 🟢 Buying {pair} @ {open_rate:.8f} — stake {stake_amount:.2f} {stake_currency} ({enter_tag})")
            },
            entry_fill: {
                title: "Freqtrade — entry filled",
                message: ("\($mode_tag) ✅ Bought {pair} @ {open_rate:.8f} — stake {stake_amount:.2f} {stake_currency}")
            },
            entry_cancel: {
                title: "Freqtrade — entry cancelled",
                message: ("\($mode_tag) 🚫 Entry order for {pair} cancelled (unfilled)")
            },
            exit: {
                title: "Freqtrade — exit",
                message: ("\($mode_tag) 🔴 Selling {pair} — reason: {exit_reason}")
            },
            exit_fill: {
                title: "Freqtrade — exit filled",
                message: ("\($mode_tag) 💰 Sold {pair} @ {close_rate:.8f} — profit {profit_ratio:.2%} ({profit_amount:.2f} {stake_currency}) — reason: {exit_reason}")
            },
            exit_cancel: {
                title: "Freqtrade — exit cancelled",
                message: ("\($mode_tag) 🚫 Exit order for {pair} cancelled")
            },
            # Freqtrade looks these keys up by RPCMessageType value, so
            # `status` alone would NOT cover startup/warning/exception — each
            # needs its own entry. All three carry a `{status}` placeholder.
            status: {
                title: "Freqtrade — status",
                message: ("\($mode_tag) ℹ️ {status}")
            },
            startup: {
                title: "Freqtrade — startup",
                message: ("\($mode_tag) 🚀 {status}")
            },
            warning: {
                title: "Freqtrade — warning",
                message: ("\($mode_tag) ⚠️ {status}")
            },
            exception: {
                title: "Freqtrade — bot error",
                message: ("\($mode_tag) ❗ The bot hit an exception and stopped trading:\n{status}")
            },
            strategy_msg: {
                title: "Freqtrade — strategy",
                message: ("\($mode_tag) 📣 {msg}")
            },
            protection_trigger: {
                title: "Freqtrade — protection triggered",
                message: ("\($mode_tag) 🛑 Protection triggered for {pair}: {reason}")
            },
            protection_trigger_global: {
                title: "Freqtrade — circuit breaker",
                message: ("\($mode_tag) 🛑 Global protection triggered: {reason} — trading paused")
            }
        },
        api_server: {
            enabled: true,
            listen_ip_address: "0.0.0.0",
            listen_port: 8080,
            verbosity: "error",
            enable_openapi: false,
            jwt_secret_key: $jwt_secret,
            ws_token: $ws_token,
            CORS_origins: $cors,
            username: $api_username,
            password: $api_password
        },
        initial_state: "running",
        force_entry_enable: false,
        internals: { process_throttle_secs: 5 }
    }' > "$FT_CONFIG"
chmod 600 "$FT_CONFIG"

# Backtest/hyperopt variant: no webhook spam, no API port clash, always dry.
jq 'del(.webhook)
    | .api_server.enabled = false
    | .dry_run = true' "$FT_CONFIG" > "$BT_CONFIG"
chmod 600 "$BT_CONFIG"
info "Freqtrade configuration generated ($FT_CONFIG, backtest variant $BT_CONFIG)."

# --------------------------------------------------------------- nginx ------
# Two roles:
#  1. :8099  — HA ingress: serves the bundled control panel and proxies
#              /proxy/* to the Freqtrade API, injecting Basic auth server-side
#              (access is already gated by Home Assistant's own login).
#  2. :8124  — localhost-only relay that lets Freqtrade's webhook reach the
#              HA Core notify service (freqtrade cannot send the required
#              Authorization header itself).
BASIC_B64="$(printf '%s:%s' "$API_USERNAME" "$API_PASSWORD" | base64 -w0)"

cat > "$NGINX_CONF" <<NGINXEOF
worker_processes 1;
pid /run/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 128; }
http {
    include /etc/nginx/mime.types;
    access_log off;
    server_tokens off;

    server {
        listen 8099 default_server;
        # Only the Home Assistant ingress gateway may talk to this port.
        allow 172.30.32.2;
        deny all;

        root /opt/ha-panel;
        location = / {
            add_header Cache-Control "no-store";
            try_files /index.html =404;
        }
        location /control/ {
            proxy_pass http://127.0.0.1:8125/;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            # A download or backtest run can take a long time; the panel polls
            # /control/status, but the initial POST must not time out either.
            proxy_read_timeout 600s;
        }
        location /proxy/ {
            proxy_pass http://127.0.0.1:8080/;
            proxy_set_header Authorization "Basic ${BASIC_B64}";
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_read_timeout 90s;
        }
    }

    server {
        listen 127.0.0.1:8124;
        location = /notify {
            proxy_pass http://supervisor/core/api/services/${NOTIFY_DOMAIN}/${NOTIFY_NAME};
            proxy_set_header Authorization "Bearer ${SUPERVISOR_TOKEN:-}";
            proxy_set_header Content-Type "application/json";
            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }
    }
}
NGINXEOF
chmod 600 "$NGINX_CONF"

nginx -t -c "$NGINX_CONF" >/dev/null 2>&1 || fatal "Generated nginx configuration is invalid (run 'nginx -t' in the container for details)"
nginx -c "$NGINX_CONF"
info "nginx started (ingress panel on :8099, notification relay on 127.0.0.1:8124)."

# The panel's Backtesting card drives this: Freqtrade's own backtest API is
# webserver-mode only, so it cannot be served by the trading bot's API.
# Probe the endpoint rather than the PID — a process that is alive but not
# serving would otherwise be reported as healthy. Never fatal: a broken panel
# feature must not stop the bot from trading.
python3 /opt/ha-panel/control.py &
CONTROL_READY="no"
for _ in $(seq 1 10); do
    if curl -fsS -m 2 -o /dev/null "http://127.0.0.1:8125/status"; then
        CONTROL_READY="yes"; break
    fi
    sleep 1
done
if [[ "$CONTROL_READY" == "yes" ]]; then
    info "Backtest control endpoint started (127.0.0.1:8125, panel only)."
else
    warn "Backtest control endpoint did not come up — the panel's Backtesting card will be unavailable."
    warn "Trading is unaffected; the ft-* helpers still work from a container shell."
fi

# ------------------------------------------------------ start notifications --
LAST_MODE="$(cat "$STATE_DIR/last_mode" 2>/dev/null || echo "")"
if [[ -n "$LAST_MODE" && "$LAST_MODE" != "$MODE" && "$FORCED_DRY_RUN" == "no" ]]; then
    warn "Mode changed: $LAST_MODE -> $MODE"
    ha_notify "Freqtrade — mode changed" "Trading mode changed: ${LAST_MODE} → ${MODE}."
fi
echo "$MODE" > "$STATE_DIR/last_mode"

if [[ "$MODE" == "live" ]]; then
    info "=================================================================="
    info "  STARTING IN *** LIVE *** MODE — REAL MONEY IS AT RISK"
    info "  stake/trade: ${STAKE_AMT} ${STAKE_CCY} (~${STAKE_EUR} EUR)"
    info "  exposure cap: ${EXPOSURE_AMT} ${STAKE_CCY} (~${EXPOSURE_EUR} EUR), max trades: ${MAX_OPEN_TRADES}"
    info "=================================================================="
else
    info "Starting in DRY-RUN mode (simulated wallet: ${DRY_WALLET} ${STAKE_CCY}). No real orders will be placed."
fi
ha_notify "Freqtrade started ${MODE_TAG}" \
    "Bot starting in ${MODE^^} mode (${STRATEGY}). Stake ${STAKE_AMT} ${STAKE_CCY} (~${STAKE_EUR} EUR)/trade, max ${MAX_OPEN_TRADES} trades, exposure cap ${EXPOSURE_AMT} ${STAKE_CCY}."

# ------------------------------------------------------------- freqtrade ----
VERBOSITY=()
[[ "$LOG_LEVEL" == "debug" ]] && VERBOSITY=(-v)

info "Launching freqtrade..."
exec freqtrade trade \
    --config "$FT_CONFIG" \
    --userdir "$USER_DATA" \
    --strategy "$STRATEGY" \
    "${VERBOSITY[@]}"
