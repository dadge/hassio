#!/usr/bin/env bash
# ==============================================================================
# Regression tests for the bundled ft-* helpers.
#
# They are executed for real against shimmed `freqtrade`/`date` commands and a
# sandboxed /data, so no container, network or exchange account is needed.
#
# The case that motivated this file: ft-download-data discarded freqtrade's
# stderr, and under `set -e` a failing command substitution aborted the script
# before any message printed — the panel showed "exit 2" and nothing else.
#
#   Requirements: bash, jq
#   Usage:        freqtrade_okx/tests/test_ft_helpers.sh
# ==============================================================================
set -Euo pipefail

ADDON="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$ADDON/rootfs/usr/local/bin"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

command -v jq >/dev/null || { echo "missing required tool: jq" >&2; exit 2; }

fails=0
check() {
    if [[ "$2" == "0" ]]; then printf '  \033[32mok\033[0m   %s\n' "$1"
    else printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails + 1)); fi
}
has()   { grep -Fq -- "$2" <<<"$1" && echo 0 || echo 1; }
hasnt() { grep -Fq -- "$2" <<<"$1" && echo 1 || echo 0; }
scenario() { printf '\n\033[1;34m%s\033[0m\n' "$*"; }

CONFIG='{"stake_currency":"USDC","exchange":{"name":"myokx","pair_blacklist":["A/USDC","[A-Z0-9]+(UP|DOWN|BULL|BEAR)/USDC"]},"pairlists":[{"method":"VolumePairList"}]}'

setup() {
    rm -rf "${ROOT:?}"/*
    mkdir -p "$ROOT/data/user_data" "$ROOT/bin"
    jq -n "$CONFIG" > "$ROOT/data/config.json"
    jq -n "$CONFIG" > "$ROOT/data/config_backtest.json"
    # Rewrite the container-absolute paths into the sandbox.
    sed -e "s#/data/#$ROOT/data/#g" "$BIN/$1" > "$ROOT/$1"
    chmod +x "$ROOT/$1"
}
shim() { printf '%s\n' "$2" > "$ROOT/bin/$1"; chmod +x "$ROOT/bin/$1"; }
run_helper() {
    rm -f "$ROOT/args.txt"
    OUT="$(PATH="$ROOT/bin:$PATH" ARGS_FILE="$ROOT/args.txt" bash "$ROOT/$1" "${@:2}" 2>&1)"         && RC=0 || RC=$?
}
# Args the shimmed freqtrade saw, even for calls whose output the helper
# captures to a file rather than printing.
seen_args() { cat "$ROOT/args.txt" 2>/dev/null || echo ""; }

# ---------------------------------------------------------------------------
scenario "ft-download-data surfaces freqtrade's own error instead of a bare exit code"
setup ft-download-data
# freqtrade exits 2 on OperationalException, printing to stderr.
shim freqtrade '#!/usr/bin/env bash
echo "2026-08-13 12:00:00 - freqtrade - ERROR - OperationalException: Pairlist Handler VolumePairList requires exchange to have market data" >&2
exit 2'
run_helper ft-download-data 240
check "exits non-zero"                    "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"
check "quotes the exchange error"         "$(has "$OUT" "OperationalException: Pairlist Handler")"
check "explains what to try"              "$(has "$OUT" "Common causes")"
check "mentions the MiCA/USDC case"       "$(has "$OUT" "--stake-currency USDC")"
check "does not claim pairs were found"   "$(hasnt "$OUT" "pairs selected")"

scenario "an empty or non-JSON pairlist is reported, not passed on"
setup ft-download-data
shim freqtrade '#!/usr/bin/env bash
echo "Traceback (most recent call last):"
exit 0'
run_helper ft-download-data 240
check "exits non-zero"                    "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"
check "shows the unusable output"         "$(has "$OUT" "Traceback")"
check "no static config written"          "$([[ ! -f $ROOT/data/config_backtest_static.json ]] && echo 0 || echo 1)"
setup ft-download-data
shim freqtrade '#!/usr/bin/env bash
echo "[]"'
run_helper ft-download-data 240
check "an empty pairlist is a failure"    "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"

scenario "a resolved pairlist becomes a static backtest config"
setup ft-download-data
# shellcheck disable=SC2016  # shim body must stay unexpanded
shim freqtrade '#!/usr/bin/env bash
case "$1" in
  test-pairlist) echo "$*" >> "$ARGS_FILE"; echo "Pairs:"; echo "[\"BTC/USDC\",\"ETH/USDC\"]" ;;
  download-data) echo "DOWNLOAD-ARGS: $*" ;;
esac'
run_helper ft-download-data 240
check "succeeds"                          "$([[ $RC -eq 0 ]] && echo 0 || echo 1)"
check "reports the pair count"            "$(has "$OUT" "2 pairs selected")"
# freqtrade resolves user_data against the cwd (/) unless told otherwise,
# which aborted every pairlist resolution with "Directory /user_data does not exist".
check "test-pairlist gets --userdir"      "$(has "$(seen_args)" "--userdir /")"
check "downloads with the static config"  "$(has "$OUT" "config_backtest_static.json")"
check "whitelist written"                 "$([[ $(jq -r '.exchange.pair_whitelist | join(",")' "$ROOT/data/config_backtest_static.json") == "BTC/USDC,ETH/USDC" ]] && echo 0 || echo 1)"
check "pairlist switched to static"       "$([[ $(jq -r '.pairlists[0].method' "$ROOT/data/config_backtest_static.json") == StaticPairList ]] && echo 0 || echo 1)"

scenario "--stake-currency rewrites the quote everywhere it matters"
setup ft-download-data
# shellcheck disable=SC2016  # shim body must stay unexpanded
shim freqtrade '#!/usr/bin/env bash
case "$1" in
  test-pairlist) echo "[\"BTC/USDT\"]" ;;
  download-data) echo "DOWNLOAD-ARGS: $*" ;;
esac'
run_helper ft-download-data 240 --stake-currency USDT
check "succeeds"                          "$([[ $RC -eq 0 ]] && echo 0 || echo 1)"
check "announces the override"            "$(has "$OUT" "Overriding the stake currency for this run: USDT")"
check "stake currency rewritten"          "$([[ $(jq -r .stake_currency "$ROOT/data/config_backtest_static.json") == USDT ]] && echo 0 || echo 1)"
check "quote-specific blacklist rewritten" "$(has "$(jq -r '.exchange.pair_blacklist | join(" ")' "$ROOT/data/config_backtest_static.json")" "BEAR)/USDT")"
check "unrelated blacklist entry intact"  "$(has "$(jq -r '.exchange.pair_blacklist | join(" ")' "$ROOT/data/config_backtest_static.json")" "A/USDT")"
run_helper ft-download-data 240 --stake-currency ETH
check "rejects an unsupported currency"   "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"
run_helper ft-download-data notanumber
check "rejects a non-numeric day count"   "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"

scenario "the strategy timeframe is not overridden"
setup ft-download-data
# shellcheck disable=SC2016  # shim body must stay unexpanded
shim freqtrade '#!/usr/bin/env bash
case "$1" in
  test-pairlist) echo "[\"BTC/USDC\"]" ;;
  download-data) echo "DOWNLOAD-ARGS: $*" ;;
esac'
run_helper ft-download-data 30
check "downloads 15m and 1h by default"   "$(has "$OUT" "--timeframes 15m 1h")"
run_helper ft-download-data 30 --timeframes "5m"
check "honours --timeframes"              "$(has "$OUT" "--timeframes 5m")"
# shellcheck disable=SC2016  # the injection probe must reach the helper unexpanded
run_helper ft-download-data 30 --timeframes '$(id)'
check "rejects an injected timeframe"     "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"

setup ft-backtest
jq -n '{stake_currency:"USDC",strategy:"MeanRevert15m"}' > "$ROOT/data/config.json"
jq -n '{stake_currency:"USDC"}' > "$ROOT/data/config_backtest_static.json"
shim freqtrade '#!/usr/bin/env bash
echo "BACKTEST-ARGS: $*"'
run_helper ft-backtest
check "never forces a timeframe"          "$(hasnt "$OUT" "--timeframe")"
check "uses the configured strategy"      "$(has "$OUT" "--strategy MeanRevert15m")"
run_helper ft-backtest --strategy ReboundStrategy
check "--strategy overrides it"           "$(has "$OUT" "--strategy ReboundStrategy")"
run_helper ft-backtest --strategy 'evil; rm -rf /'
check "rejects an injected strategy name" "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"

scenario "ft-backtest refuses data downloaded for another quote currency"
setup ft-backtest
jq -n '{stake_currency:"USDC"}' > "$ROOT/data/config_backtest_static.json"
shim freqtrade '#!/usr/bin/env bash
echo "BACKTEST-ARGS: $*"'
run_helper ft-backtest --stake-currency USDT
check "exits non-zero"                    "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"
check "names both currencies"             "$(has "$OUT" "downloaded data is for USDC pairs, not USDT")"
check "tells you how to fix it"           "$(has "$OUT" "ft-download-data --stake-currency USDT")"
check "never ran a backtest"              "$(hasnt "$OUT" "BACKTEST-ARGS")"
run_helper ft-backtest --stake-currency USDC
check "matching currency runs"            "$([[ $RC -eq 0 ]] && echo 0 || echo 1)"
check "passes the static config"          "$(has "$OUT" "config_backtest_static.json")"

scenario "ft-backtest without downloaded data explains what to run"
setup ft-backtest
rm -f "$ROOT/data/config_backtest_static.json"
run_helper ft-backtest
check "exits non-zero"                    "$([[ $RC -ne 0 ]] && echo 0 || echo 1)"
check "points at ft-download-data"        "$(has "$OUT" "Run ft-download-data first")"

printf '\n========================================\n'
if [[ $fails -eq 0 ]]; then printf '\033[32mALL CHECKS PASSED\033[0m\n'
else printf '\033[31m%d CHECK(S) FAILED\033[0m\n' "$fails"; fi
exit $((fails > 0))
