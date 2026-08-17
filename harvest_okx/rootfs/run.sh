#!/usr/bin/env bash
# ==============================================================================
# OKX Volatility Harvester — Home Assistant add-on entrypoint
#
# Responsibilities:
#   1. Read & validate add-on options (/data/options.json)
#   2. Enforce the paper/live safety rules (dry-run by default, forced dry-run
#      after every add-on update, explicit confirmation for live)
#   3. Write the resolved runtime config the bot reads (secrets never logged)
#   4. exec the bot, which serves the ingress panel itself on :8099
#
# Deliberately simpler than the freqtrade add-on's entrypoint: this bot is a
# single Python process that serves its own panel, so there is no nginx and no
# API-credential relay to set up.
# ==============================================================================
set -Eeuo pipefail

OPTIONS_FILE=/data/options.json
STATE_DIR=/data/.addon
RUNTIME_FILE="$STATE_DIR/runtime.json"

log()   { printf '%s [%-5s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "${*:2}" >&2; }
info()  { log INFO "$@"; }
warn()  { log WARN "$@"; }
fatal() { log FATAL "$@"; log FATAL "Add-on stopped. Fix the configuration and restart."; exit 1; }

[[ -f "$OPTIONS_FILE" ]] || fatal "Options file $OPTIONS_FILE not found (is this running as an HA add-on?)"

opt() { jq -r --arg k "$1" 'if .[$k] == null then "" else (.[$k] | tostring) end' "$OPTIONS_FILE"; }

MODE="$(opt mode)"
I_UNDERSTAND="$(opt i_understand_live_trading)"
OKX_KEY="$(opt okx_api_key)"
OKX_SECRET="$(opt okx_api_secret)"
OKX_PASSPHRASE="$(opt okx_api_passphrase)"
NOTIFY_SERVICE="$(opt notify_service)"
NOTIFY_ENABLED="$(opt notifications_enabled)"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

ha_notify() {  # $1 = title, $2 = message
    [[ "$NOTIFY_ENABLED" == "true" ]] || return 0
    local domain="${NOTIFY_SERVICE%%.*}" name="${NOTIFY_SERVICE#*.}" payload
    payload="$(jq -cn --arg t "$1" --arg m "$2" '{title: $t, message: $m}')"
    curl -fsS -m 10 -o /dev/null -X POST \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN:-}" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "http://supervisor/core/api/services/${domain}/${name}" \
        || warn "Could not send HA notification via '${NOTIFY_SERVICE}'"
}

# ------------------------------------------------------- basic validation ---
[[ "$MODE" == "dry-run" || "$MODE" == "live" ]] || fatal "Invalid mode '$MODE' (must be dry-run or live)"
[[ "$NOTIFY_SERVICE" == *.* ]] || fatal "notify_service must look like 'notify.mobile_app_xxx' (got '$NOTIFY_SERVICE')"

# ---------------------------------------- forced dry-run after add-on update --
# Same rule as the freqtrade add-on: a new version must never inherit live
# trading. If the Supervisor write fails we refuse live until the user
# re-saves the options themselves.
CURRENT_VERSION="${ADDON_VERSION:-unknown}"
LAST_VERSION="$(cat "$STATE_DIR/last_version" 2>/dev/null || echo "")"
FORCED_DRY_RUN="no"
if [[ -n "$LAST_VERSION" && "$LAST_VERSION" != "$CURRENT_VERSION" && "$MODE" == "live" ]]; then
    warn "Add-on updated ($LAST_VERSION -> $CURRENT_VERSION) — forcing dry-run."
    MODE="dry-run"
    FORCED_DRY_RUN="yes"
    new_opts="$(jq -c '{options: (. + {mode: "dry-run", i_understand_live_trading: false})}' "$OPTIONS_FILE")"
    if curl -fsS -m 10 -o /dev/null -X POST \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN:-}" \
        -H "Content-Type: application/json" \
        -d "$new_opts" "http://supervisor/addons/self/options"; then
        info "Stored add-on options were reset to dry-run."
    else
        warn "Could not reset stored options via the Supervisor API."
        warn "  set mode=dry-run, save, then set mode=live again if you really want live trading."
    fi
    ha_notify "Harvester — forced dry-run" \
        "Add-on updated to ${CURRENT_VERSION}; trading was reset to dry-run for safety."
fi
echo "$CURRENT_VERSION" > "$STATE_DIR/last_version"

# ------------------------------------------------------------ live gating ---
if [[ "$MODE" == "live" ]]; then
    [[ "$I_UNDERSTAND" == "true" ]] || fatal "mode=live requires the option 'i_understand_live_trading: true'."
    [[ -n "$OKX_KEY" && -n "$OKX_SECRET" && -n "$OKX_PASSPHRASE" ]] \
        || fatal "mode=live requires okx_api_key, okx_api_secret and okx_api_passphrase."
elif [[ -n "$OKX_KEY" ]]; then
    info "OKX credentials present but unused — dry-run reads public market data only."
else
    info "No OKX credentials configured — fine for dry-run (public market data only)."
fi

# ------------------------------------------------- resolved runtime config --
# Written to $STATE_DIR (0700) rather than passed as argv or environment, so
# the API secret never shows up in `ps` output.
jq '{
      mode: $mode,
      okx_environment, quote_currency,
      okx_api_key, okx_api_secret, okx_api_passphrase,
      basket_size, target_exposure_pct, rebalance_band_pct,
      volatility_lookback_days, reselect_days, min_volume_usdt, min_order_usdt,
      paper_wallet_usdt, paper_slippage_model, paper_slippage_pct,
      live_max_deployed_usdt, check_interval_minutes,
      notifications_enabled, notify_service, log_level
    }' --arg mode "$MODE" "$OPTIONS_FILE" > "$RUNTIME_FILE"
chmod 600 "$RUNTIME_FILE"

LAST_MODE="$(cat "$STATE_DIR/last_mode" 2>/dev/null || echo "")"
if [[ -n "$LAST_MODE" && "$LAST_MODE" != "$MODE" && "$FORCED_DRY_RUN" == "no" ]]; then
    warn "Mode changed: $LAST_MODE -> $MODE"
    ha_notify "Harvester — mode changed" "Trading mode changed: ${LAST_MODE} → ${MODE}."
fi
echo "$MODE" > "$STATE_DIR/last_mode"

if [[ "$MODE" == "live" ]]; then
    info "=================================================================="
    info "  STARTING IN *** LIVE *** MODE — REAL MONEY IS AT RISK"
    info "  exposure target: $(opt target_exposure_pct)% of wallet"
    info "  deployment cap:  $(opt live_max_deployed_usdt) USDT"
    info "=================================================================="
else
    info "Starting in DRY-RUN mode (paper wallet: $(opt paper_wallet_usdt) $(opt quote_currency)). No real orders will be placed."
fi

info "Launching harvester..."
exec python3 /opt/harvest/bot.py
