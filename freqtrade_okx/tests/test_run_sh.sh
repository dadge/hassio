#!/usr/bin/env bash
# ==============================================================================
# Regression tests for the add-on entrypoint (freqtrade_okx/rootfs/run.sh).
#
# These cover the safety-critical behaviour that must never regress:
# live-mode gating, forced dry-run after an update, credential handling,
# EUR->USDT conversion (including the refusal to guess a rate), budget
# warnings and user-file preservation.
#
# run.sh is executed for real; only its container-absolute paths are rewritten
# into a sandbox, and curl / nginx / freqtrade are replaced by shims. No
# network access and no container are needed.
#
#   Requirements: bash, jq, awk, sed, sha256sum, base64
#   Usage:        freqtrade_okx/tests/test_run_sh.sh
# ==============================================================================
set -Euo pipefail

ADDON="$(cd "$(dirname "$0")/.." && pwd)"
RUN_SH="$ADDON/rootfs/run.sh"
DEFAULTS="$ADDON/rootfs/defaults"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

for tool in jq awk sed sha256sum base64; do
    command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 2; }
done

fails=0
green() { printf '  \033[32mok\033[0m   %s\n' "$1"; }
red()   { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails + 1)); }
check() { [[ "$2" == "0" ]] && green "$1" || red "$1"; }
has()   { grep -Fq -- "$2" <<<"$1" && echo 0 || echo 1; }
hasnt() { grep -Fq -- "$2" <<<"$1" && echo 1 || echo 0; }
scenario() { printf '\n\033[1;34m%s\033[0m\n' "$*"; }

DEFAULT_OPTIONS='{
  "mode": "dry-run", "i_understand_live_trading": false, "okx_environment": "okx",
  "okx_api_key": "", "okx_api_secret": "", "okx_api_passphrase": "",
  "stake_amount_eur": 20.0, "max_total_exposure_eur": 100.0, "max_open_trades": 3,
  "dry_run_wallet_usdt": 1000.0, "pairlist_min_volume_usdt": 1000000.0,
  "api_username": "freqtrade", "api_password": "", "notifications_enabled": true,
  "notify_service": "notify.mobile_app_op15", "cors_origins": [], "log_level": "info"
}'

# ------------------------------------------------------------------ sandbox --
# Canned OKX ticker; every Supervisor/HA call fails, as it would outside HAOS.
CURL_DEFAULT='#!/usr/bin/env bash
for a in "$@"; do case "$a" in http*) url="$a";; esac; done
case "$url" in
  *EUR-USDT*) echo "{\"code\":\"0\",\"data\":[{\"instId\":\"EUR-USDT\",\"last\":\"1.1742\"}]}"; exit 0 ;;
  *) echo "test-shim: refusing $url" >&2; exit 7 ;;
esac'

setup() {
    rm -rf "$ROOT"/*
    mkdir -p "$ROOT/data" "$ROOT/etc/nginx" "$ROOT/bin" "$ROOT/defaults"
    cp -r "$DEFAULTS/." "$ROOT/defaults/"
    jq -n "$DEFAULT_OPTIONS" > "$ROOT/data/options.json"
    shim curl "$CURL_DEFAULT"
    # nginx: accept anything (the container smoke test does the real -t check).
    shim nginx '#!/usr/bin/env bash
exit 0'
    shim freqtrade '#!/usr/bin/env bash
echo "FREQTRADE-ARGS: $*"'
    sed -e "s#^OPTIONS_FILE=/data/options.json#OPTIONS_FILE=$ROOT/data/options.json#" \
        -e "s#^STATE_DIR=/data/.addon#STATE_DIR=$ROOT/data/.addon#" \
        -e "s#^USER_DATA=/data/user_data#USER_DATA=$ROOT/data/user_data#" \
        -e "s#^FT_CONFIG=/data/config.json#FT_CONFIG=$ROOT/data/config.json#" \
        -e "s#^BT_CONFIG=/data/config_backtest.json#BT_CONFIG=$ROOT/data/config_backtest.json#" \
        -e "s#^NGINX_CONF=/etc/nginx/nginx.conf#NGINX_CONF=$ROOT/etc/nginx/nginx.conf#" \
        -e "s#/defaults/#$ROOT/defaults/#g" \
        -e "s#^ *sleep 15\$#    sleep 0#" \
        "$RUN_SH" > "$ROOT/run.sh"
    # Guard against a silently ineffective rewrite.
    grep -q "$ROOT/data/options.json" "$ROOT/run.sh" || { echo "path rewrite failed" >&2; exit 2; }
}
shim()   { printf '%s\n' "$2" > "$ROOT/bin/$1"; chmod +x "$ROOT/bin/$1"; }
set_opt() { jq "$1" "$ROOT/data/options.json" > "$ROOT/data/o.tmp" && mv "$ROOT/data/o.tmp" "$ROOT/data/options.json"; }
cfg()    { jq -r "$1" "$ROOT/data/config.json"; }
run_addon() {
    OUT="$(PATH="$ROOT/bin:$PATH" ADDON_VERSION="${1:-1.0.0}" SUPERVISOR_TOKEN=test-token \
           bash "$ROOT/run.sh" 2>&1)" && RC=0 || RC=$?
}

# ---------------------------------------------------------------------------
scenario "live mode without the second confirmation must refuse to start"
setup; set_opt '.mode = "live"'; run_addon
check "exits non-zero"                    "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"
check "explains the missing confirmation" "$(has "$OUT" "requires the option 'i_understand_live_trading: true'")"
check "freqtrade never launched"          "$(hasnt "$OUT" "FREQTRADE-ARGS")"
check "no config written"                 "$([[ ! -f $ROOT/data/config.json ]] && echo 0 || echo 1)"

scenario "live mode confirmed but incomplete credentials must refuse to start"
setup; set_opt '.mode = "live" | .i_understand_live_trading = true'; run_addon
check "names the missing api key"         "$(has "$OUT" "mode=live requires okx_api_key")"
setup; set_opt '.mode = "live" | .i_understand_live_trading = true
                | .okx_api_key = "k" | .okx_api_secret = "s"'; run_addon
check "names the missing passphrase"      "$(has "$OUT" "mode=live requires okx_api_passphrase")"

scenario "fully configured live mode starts live"
setup; set_opt '.mode = "live" | .i_understand_live_trading = true
                | .okx_api_key = "k" | .okx_api_secret = "s" | .okx_api_passphrase = "p"'
run_addon
check "starts successfully"                "$([[ $RC -eq 0 ]] && echo 0 || echo 1)"
check "shouts the live warning"            "$(has "$OUT" "REAL MONEY IS AT RISK")"
check "dry_run = false in the config"      "$([[ $(cfg .dry_run) == false ]] && echo 0 || echo 1)"
check "live trades use their own DB"       "$(has "$(cfg .db_url)" "tradesv3.sqlite")"
check "credentials reach the config"       "$([[ $(cfg .exchange.password) == p ]] && echo 0 || echo 1)"
check "spot only, no margin/leverage"      "$([[ $(cfg .trading_mode) == spot && $(cfg .margin_mode) == "" ]] && echo 0 || echo 1)"
check "backtest variant stays dry-run"     "$([[ $(jq -r .dry_run "$ROOT/data/config_backtest.json") == true ]] && echo 0 || echo 1)"
check "backtest variant has no webhook"    "$([[ $(jq -r '.webhook // "none"' "$ROOT/data/config_backtest.json") == none ]] && echo 0 || echo 1)"

scenario "an add-on update while live forces dry-run"
setup; set_opt '.mode = "live" | .i_understand_live_trading = true
                | .okx_api_key = "k" | .okx_api_secret = "s" | .okx_api_passphrase = "p"'
mkdir -p "$ROOT/data/.addon"; echo "0.9.0" > "$ROOT/data/.addon/version"
run_addon 1.0.0
check "warns about the forced dry-run"     "$(has "$OUT" "SAFETY: forcing DRY-RUN")"
check "dry_run = true in the config"       "$([[ $(cfg .dry_run) == true ]] && echo 0 || echo 1)"
check "dry-run trade DB is used"           "$(has "$(cfg .db_url)" "tradesv3.dryrun.sqlite")"
check "reports the failed option reset"    "$(has "$OUT" "Could not persist the option reset")"
check "keeps the stale version marker"     "$([[ $(cat "$ROOT/data/.addon/version") == 0.9.0 ]] && echo 0 || echo 1)"
run_addon 1.0.0
check "keeps forcing dry-run until re-saved" "$(has "$OUT" "SAFETY: forcing DRY-RUN")"

scenario "unchanged version in dry-run: quiet start"
setup; run_addon 1.0.0
check "no forced dry-run warning"          "$(hasnt "$OUT" "SAFETY: forcing DRY-RUN")"
check "version marker written"             "$([[ $(cat "$ROOT/data/.addon/version") == 1.0.0 ]] && echo 0 || echo 1)"
check "freqtrade launched with the strategy" "$(has "$OUT" "--strategy ReboundStrategy")"
check "API password generated and stored"  "$([[ -s $ROOT/data/.addon/api_password ]] && echo 0 || echo 1)"
check "API password not logged"            "$(hasnt "$OUT" "$(cat "$ROOT/data/.addon/api_password")")"

scenario "a mode change is announced"
setup; run_addon >/dev/null 2>&1
set_opt '.mode = "live" | .i_understand_live_trading = true
         | .okx_api_key = "k" | .okx_api_secret = "s" | .okx_api_passphrase = "p"'
run_addon
check "logs the transition"                "$(has "$OUT" "Mode changed: dry-run -> live")"

scenario "invalid options fail fast with a clear message"
setup; set_opt '.mode = "paper"'; run_addon
check "invalid mode rejected"              "$(has "$OUT" "Invalid mode 'paper'")"
setup; set_opt '.notify_service = "mobile_app_op15"'; run_addon
check "malformed notify_service rejected"  "$(has "$OUT" "notify_service must look like")"
setup; set_opt '.api_username = ""'; run_addon
check "empty api_username rejected"        "$(has "$OUT" "api_username must not be empty")"

scenario "budget conversion and sanity warnings"
setup; run_addon
check "20 EUR at 1.1742 -> 23.48 USDT"     "$([[ $(cfg .stake_amount) == 23.48 ]] && echo 0 || echo 1)"
check "100 EUR cap -> 117.42 USDT"         "$([[ $(cfg .available_capital) == 117.42 ]] && echo 0 || echo 1)"
setup; set_opt '.stake_amount_eur = 2 | .max_total_exposure_eur = 3'; run_addon
check "warns about a tiny stake"           "$(has "$OUT" "may reject entries on some pairs")"
check "warns exposure < stake x trades"    "$(has "$OUT" "exceeds max_total_exposure_eur")"
setup; set_opt '.max_total_exposure_eur = 5000'; run_addon
check "dry-run caps capital to the wallet" "$([[ $(cfg .available_capital) == 1000.0 ]] && echo 0 || echo 1)"

scenario "a locally edited strategy is never overwritten"
setup; run_addon >/dev/null 2>&1
echo "# my tweak" >> "$ROOT/data/user_data/strategies/ReboundStrategy.py"
run_addon
check "keeps the user's file"              "$(has "$(cat "$ROOT/data/user_data/strategies/ReboundStrategy.py")" "# my tweak")"
check "warns about the divergence"         "$(has "$OUT" "Keeping your locally modified ReboundStrategy.py")"

scenario "no EUR/USDT rate: refuse to start rather than guess"
setup; shim curl '#!/usr/bin/env bash
exit 7'
run_addon
check "exits non-zero"                     "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"
check "explains the missing rate"          "$(has "$OUT" "Could not determine the EUR/USDT exchange rate")"
check "retried before giving up"           "$(has "$OUT" "attempt 11/12")"
check "no config written"                  "$([[ ! -f $ROOT/data/config.json ]] && echo 0 || echo 1)"

scenario "OKX ticker down, ECB reachable: documented fallback"
setup; shim curl '#!/usr/bin/env bash
for a in "$@"; do case "$a" in http*) url="$a";; esac; done
case "$url" in
  *frankfurter*) echo "{\"rates\":{\"USD\":1.09}}"; exit 0 ;;
  *) exit 7 ;;
esac'
run_addon
check "names the fallback source"          "$(has "$OUT" "using the ECB EUR/USD reference rate")"
check "20 EUR at 1.09 -> 21.80 USDT"       "$([[ $(cfg .stake_amount) == 21.80 ]] && echo 0 || echo 1)"

scenario "implausible rate responses are rejected"
setup; shim curl '#!/usr/bin/env bash
for a in "$@"; do case "$a" in http*) url="$a";; esac; done
case "$url" in
  *EUR-USDT*) echo "{\"code\":\"1\",\"msg\":\"error\",\"data\":[]}"; exit 0 ;;
  *frankfurter*) echo "{\"rates\":{\"USD\":\"not-a-number\"}}"; exit 0 ;;
  *) exit 7 ;;
esac'
run_addon
check "refuses to start"                   "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"
check "no config written"                  "$([[ ! -f $ROOT/data/config.json ]] && echo 0 || echo 1)"

printf '\n========================================\n'
if [[ $fails -eq 0 ]]; then
    printf '\033[32mALL CHECKS PASSED\033[0m\n'
else
    printf '\033[31m%d CHECK(S) FAILED\033[0m\n' "$fails"
fi
exit $((fails > 0))
