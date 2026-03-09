#!/usr/bin/env bash
set -e

CONFIG_PATH=/data/options.json

echo "Starting Binance Grid Bot Dashboard..."

# Default mode for production is 'live'
MODE="live"
PASSWORD=""
BINANCE_API_KEY=""
BINANCE_SECRET_KEY=""

if [ -f "$CONFIG_PATH" ]; then
  BINANCE_API_KEY=$(jq -r '.binance_api_key // ""' $CONFIG_PATH)
  BINANCE_SECRET_KEY=$(jq -r '.binance_secret_key // ""' $CONFIG_PATH)
  MODE=$(jq -r '.mode // "live"' $CONFIG_PATH)
  PASSWORD=$(jq -r '.password // ""' $CONFIG_PATH)

  echo "Mode: ${MODE}"
  echo "Password protection: $([ -n "$PASSWORD" ] && echo 'enabled' || echo 'disabled')"

  cat > /usr/share/nginx/html/assets/config.js << EOF
window.BINANCE_CONFIG = {
  apiKey: "${BINANCE_API_KEY}",
  secretKey: "${BINANCE_SECRET_KEY}",
  mode: "${MODE}",
  password: "${PASSWORD}"
};
EOF

  echo "Configuration loaded from Home Assistant."
else
  echo "No Home Assistant config found, using default config (mode: ${MODE})."
fi

# Export MODE for the backend
export MODE="${MODE}"
export BINANCE_API_KEY="${BINANCE_API_KEY}"
export BINANCE_SECRET_KEY="${BINANCE_SECRET_KEY}"
export DATA_DIR="/data"

echo "Starting supervisor (nginx + Node.js backend)..."
echo "Backend will run in '${MODE}' mode"
echo "Database will be stored in '${DATA_DIR}'"
exec supervisord -c /etc/supervisor.d/supervisord.ini