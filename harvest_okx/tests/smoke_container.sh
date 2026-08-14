#!/usr/bin/env bash
# Packaging smoke test: build the add-on image and run the REAL entrypoint in
# it against a fake /data, the way the Supervisor does.
#
# The Python tests cover the engine; this covers everything between the
# Supervisor and the engine -- that the image has jq/curl/python, that run.sh
# parses options.json, that the safety gates actually block, and that the
# runtime config it writes is the shape bot.py expects. Those only ever fail on
# a real container.
#
# Needs Docker. No network at runtime, no API keys, and it never starts trading:
# the bot process is launched only long enough to serve one status response.
set -Eeuo pipefail

IMAGE=harvest_okx:smoke
ADDON="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; MSYS_NO_PATHCONV=1 docker rm -f harvest-smoke >/dev/null 2>&1 || true' EXIT

# Docker Desktop on Git Bash rewrites absolute paths; MSYS_NO_PATHCONV stops it
# mangling CONTAINER-side paths (/bin/bash, /data). Same helper as
# tests/smoke_container.sh.
dk() { MSYS_NO_PATHCONV=1 docker "$@"; }

# ...but HOST-side paths (build context, volume sources) must then be converted
# by hand, because Docker Desktop is a Windows binary and cannot read /d/... .
# A no-op everywhere cygpath does not exist, i.e. on Linux and in CI.
hp() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s  %s\n' "$1" "${2:-}"; FAILED=1; }
FAILED=0

# expect    NAME PATTERN TEXT -- PATTERN must appear in TEXT
# expect_no NAME PATTERN TEXT -- PATTERN must not appear in TEXT
# Spelled out rather than `grep && pass || fail`, which reads as if-then-else
# but is not: the else branch also runs when pass itself fails.
expect()    { if grep -qE "$2" <<<"$3"; then pass "$1"; else fail "$1" "$3"; fi; }
expect_no() { if grep -qE "$2" <<<"$3"; then fail "$1" "$3"; else pass "$1"; fi; }

echo "[build]"
# Built the way the Supervisor builds it, BUILD_FROM included. That arg is the
# point: the Supervisor always injects it, falling back to the Alpine HA base
# image whenever it cannot parse a base of its own, and the first install of
# this add-on failed because the Dockerfile consumed that value and then ran
# apt-get. Passing the hostile value here proves the Dockerfile ignores it.
dk build -q \
    --build-arg BUILD_VERSION=0.1.0 \
    --build-arg BUILD_ARCH=amd64 \
    --build-arg BUILD_FROM=ghcr.io/home-assistant/base:latest \
    -t "$IMAGE" "$(hp "$ADDON")" >/dev/null
pass "image builds with the Supervisor's injected BUILD_FROM"

# ---------------------------------------------------------------- helpers --
# Runs run.sh with a given options.json. The bot is replaced by a stub so the
# entrypoint's own logic is what gets tested, with no exchange calls.
run_entrypoint() {  # $1 = options json, $2 = extra docker args
    local opts="$1"
    mkdir -p "$WORK/data"
    printf '%s' "$opts" > "$WORK/data/options.json"
    dk run --rm --network none \
        -v "$(hp "$WORK/data"):/data" \
        -e ADDON_VERSION="${ADDON_VERSION_OVERRIDE:-0.1.0}" \
        --entrypoint bash "$IMAGE" -c '
            cat > /opt/harvest/bot.py <<"STUB"
import json, sys
cfg = json.load(open("/data/.addon/runtime.json"))
print("STUB_OK mode=%s basket=%s exposure=%s" % (
    cfg["mode"], cfg["basket_size"], cfg["target_exposure_pct"]))
STUB
            exec /run.sh' 2>&1
}

base_opts() {  # $1..: jq assignments applied to the defaults
    jq -cn '{
      mode: "dry-run", i_understand_live_trading: false, okx_environment: "okx",
      okx_api_key: "", okx_api_secret: "", okx_api_passphrase: "",
      basket_size: 10, target_exposure_pct: 50.0, rebalance_band_pct: 1.0,
      volatility_lookback_days: 30, reselect_days: 30, min_volume_usdt: 5000000.0,
      min_order_usdt: 5.0, paper_wallet_usdt: 1000.0, live_max_deployed_usdt: 100.0,
      check_interval_minutes: 15, notifications_enabled: false,
      notify_service: "notify.test", log_level: "info"
    }'
}

echo
echo "[image contents]"
for bin in jq curl python3; do
    if dk run --rm --network none --entrypoint bash "$IMAGE" -c "command -v $bin >/dev/null"; then
        pass "$bin present"
    else
        fail "$bin present"
    fi
done
if dk run --rm --network none --entrypoint python3 "$IMAGE" -c "import ccxt" 2>/dev/null; then
    pass "ccxt importable"
else
    fail "ccxt importable"
fi

echo
echo "[dry-run starts]"
out="$(run_entrypoint "$(base_opts)")"
expect "reaches the bot in dry-run" "STUB_OK mode=dry-run" "$out"
expect "announces paper mode" "No real orders will be placed" "$out"
expect "runtime config carries the options" "basket=10 exposure=50" "$out"

echo
echo "[live gating]"
out="$(run_entrypoint "$(base_opts | jq -c '.mode="live"')" || true)"
expect "live without confirmation is refused" "i_understand_live_trading" "$out"
expect_no "does not reach the bot" "STUB_OK" "$out"

out="$(run_entrypoint "$(base_opts | jq -c '.mode="live" | .i_understand_live_trading=true')" || true)"
expect "live without credentials is refused" "requires okx_api_key" "$out"

out="$(run_entrypoint "$(base_opts | jq -c '
    .mode="live" | .i_understand_live_trading=true |
    .okx_api_key="KEYSENTINEL01" | .okx_api_secret="SECRETSENTINEL02" | .okx_api_passphrase="PASSSENTINEL03"')")"
expect "fully-configured live announces itself" "REAL MONEY IS AT RISK" "$out"
expect "live reaches the bot" "STUB_OK mode=live" "$out"
# Distinctive sentinels above, so this proves something a one-character
# secret could not: no credential value reaches the add-on log at all.
if grep -qE 'KEYSENTINEL01|SECRETSENTINEL02|PASSSENTINEL03' <<<"$out"; then
    fail "credentials leaked into the log" \
         "$(grep -oE '[A-Z]+SENTINEL[0-9]+' <<<"$out" | sort -u | tr '\n' ' ')"
else
    pass "no credential value appears in the log"
fi

echo
echo "[forced dry-run after update]"
# First boot at 0.1.0 stores the version; second boot at 0.2.0 with live
# configured must come back up in dry-run.
mkdir -p "$WORK/data"
run_entrypoint "$(base_opts | jq -c '
    .mode="live" | .i_understand_live_trading=true |
    .okx_api_key="KEYSENTINEL01" | .okx_api_secret="SECRETSENTINEL02" | .okx_api_passphrase="PASSSENTINEL03"')" >/dev/null
ADDON_VERSION_OVERRIDE=0.2.0
out="$(run_entrypoint "$(base_opts | jq -c '
    .mode="live" | .i_understand_live_trading=true |
    .okx_api_key="KEYSENTINEL01" | .okx_api_secret="SECRETSENTINEL02" | .okx_api_passphrase="PASSSENTINEL03"')")"
unset ADDON_VERSION_OVERRIDE
expect "update forces dry-run" "forcing dry-run" "$out"
expect "bot starts in dry-run after update" "STUB_OK mode=dry-run" "$out"

echo
echo "[panel served]"
rm -rf "$WORK/data"; mkdir -p "$WORK/data"
base_opts > "$WORK/data/options.json"
dk run -d --name harvest-smoke --network none -v "$(hp "$WORK/data"):/data" \
    -e ADDON_VERSION=0.1.0 -p 18099:8099 "$IMAGE" >/dev/null
sleep 8
# The bot binds :8099 but only answers the ingress IP, so a request from the
# host must be refused -- that refusal IS the security property under test.
code="$(dk exec harvest-smoke python3 -c '
import urllib.request, urllib.error
try:
    urllib.request.urlopen("http://127.0.0.1:8099/api/status", timeout=5)
    print("200")
except urllib.error.HTTPError as e:
    print(e.code)
except Exception as e:
    print("ERR", e)' 2>&1 | tail -1)"
if [[ "$code" == "403" ]]; then pass "non-ingress client is refused (403)"; else fail "non-ingress client is refused" "got: $code"; fi
expect "panel listener started" "Ingress panel listening" "$(dk logs harvest-smoke 2>&1)"

echo
if [[ "$FAILED" == "1" ]]; then
    echo "SMOKE TEST FAILED"
    exit 1
fi
echo "smoke test passed"
