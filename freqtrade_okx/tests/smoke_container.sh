#!/usr/bin/env bash
# ==============================================================================
# Container smoke test: builds the add-on image and runs the real entrypoint
# inside it, then lets the real Freqtrade validate the generated configuration.
#
# Complements tests/test_run_sh.sh (which covers the entrypoint's logic without
# a container). This one proves the packaging: image build, nginx config +
# start, file permissions, strategy import under the real Freqtrade/TA-Lib, and
# Freqtrade's own config schema validation.
#
#   Requirements: docker (Linux engine), bash
#   Usage:        freqtrade_okx/tests/smoke_container.sh
#
# Outbound calls are stubbed: a `curl` shim returns a canned OKX USDT-EUR
# ticker (which the add-on inverts) and fails every Supervisor call, exactly
# as it would outside Home Assistant. `freqtrade trade` is replaced by an
# inspector so the test ends deterministically instead of waiting on the
# exchange.
# ==============================================================================
set -Euo pipefail

ADDON="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE=freqtrade-okx-addon:smoke
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
DATA="$WORK/data"; BIN="$WORK/bin"
mkdir -p "$DATA" "$BIN"

# /smoke/bin first so the shims win over the image's own binaries.
SMOKE_PATH=/smoke/bin:/home/ftuser/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
# Docker Desktop on Git Bash rewrites absolute paths; MSYS_NO_PATHCONV stops it.
dk() { MSYS_NO_PATHCONV=1 docker "$@"; }

step "Building the add-on image"
dk build --build-arg BUILD_VERSION=1.0.1 --build-arg BUILD_ARCH=amd64 \
    -t "$IMAGE" "$ADDON"

step "Preparing /data and the shims"
cat > "$DATA/options.json" <<'EOF'
{
  "mode": "dry-run", "i_understand_live_trading": false, "okx_environment": "okx",
  "okx_api_key": "", "okx_api_secret": "", "okx_api_passphrase": "",
  "stake_amount_eur": 20.0, "max_total_exposure_eur": 100.0, "max_open_trades": 3,
  "dry_run_wallet_usdt": 1000.0, "pairlist_min_volume_usdt": 1000000.0,
  "api_username": "freqtrade", "api_password": "", "notifications_enabled": true,
  "notify_service": "notify.mobile_app_op15", "cors_origins": [], "log_level": "info"
}
EOF

cat > "$BIN/curl" <<'EOF'
#!/bin/bash
for a in "$@"; do case "$a" in http*) url="$a";; esac; done
case "$url" in
  *USDT-EUR*) echo '{"code":"0","data":[{"instId":"USDT-EUR","last":"0.8655"}]}'; exit 0 ;;
  *) echo "smoke-shim: refusing $url" >&2; exit 7 ;;
esac
EOF

cat > "$BIN/freqtrade" <<'EOF'
#!/bin/bash
# Stands in for `freqtrade trade` and inspects what run.sh produced.
echo "run.sh handed off to: freqtrade $*"
echo "--- config.json (secrets redacted) ---"
jq '(.exchange.key, .exchange.secret, .exchange.password,
     .api_server.password, .api_server.jwt_secret_key, .api_server.ws_token)
    |= (if . == "" then "(empty)" else "***REDACTED***" end)' /data/config.json
echo "--- nginx: ingress panel + API proxy ---"
/usr/bin/curl -sS -o /dev/null -w 'panel  HTTP %{http_code}  (403 expected: not from 172.30.32.2)\n' \
    http://127.0.0.1:8099/ || true
/usr/bin/curl -sS -o /dev/null -w 'relay  HTTP %{http_code}  (502 expected: no supervisor here)\n' \
    -X POST -d '{}' http://127.0.0.1:8124/notify || true
echo "--- /data tree ---"
find /data | sort
echo "--- permissions (config + secrets must not be world-readable) ---"
stat -c '%a %n' /data/config.json /data/config_backtest.json /data/.addon /etc/nginx/nginx.conf
echo "--- strategy imports under the real Freqtrade + TA-Lib ---"
python3 -c "
import sys; sys.path.insert(0, '/data/user_data/strategies')
from ReboundStrategy import ReboundStrategy
s = ReboundStrategy(config={})
print('OK | stoploss', s.stoploss, '| roi', s.minimal_roi, '| tf', s.timeframe,
      '| protections', len(s.protections),
      '| stoploss_on_exchange', s.order_types['stoploss_on_exchange'])
"
echo "--- strategy unit tests ---"
ft-test-strategy
EOF
chmod +x "$BIN/curl" "$BIN/freqtrade"

step "Phase 1: the real entrypoint, end to end"
dk run --rm \
    -e SUPERVISOR_TOKEN=smoke-token \
    -e PATH="$SMOKE_PATH" \
    --add-host supervisor:127.0.0.1 \
    -v "$DATA":/data \
    -v "$BIN":/smoke/bin:ro \
    "$IMAGE"

step "Phase 2: Freqtrade validates the generated configs"
for cfg in /data/config.json /data/config_backtest.json; do
    echo "--- $cfg ---"
    dk run --rm --entrypoint bash -v "$DATA":/data "$IMAGE" -c \
        "freqtrade show-config --config $cfg --userdir /data/user_data" | tail -n 20
done

step "Smoke test finished"
