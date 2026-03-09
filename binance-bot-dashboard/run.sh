#!/usr/bin/env bash
set -e

echo "Starting Binance Grid Bot Dashboard..."

# Read configuration from Home Assistant options
CONFIG_PATH=/data/options.json

if [ -f "$CONFIG_PATH" ]; then
    BINANCE_API_KEY=$(jq -r '.binance_api_key // empty' $CONFIG_PATH)
    BINANCE_SECRET_KEY=$(jq -r '.binance_secret_key // empty' $CONFIG_PATH)
    MODE=$(jq -r '.mode // "live"' $CONFIG_PATH)
    PASSWORD=$(jq -r '.password // empty' $CONFIG_PATH)
else
    BINANCE_API_KEY=""
    BINANCE_SECRET_KEY=""
    MODE="live"
    PASSWORD=""
fi

echo "Mode: $MODE"
if [ -n "$PASSWORD" ]; then
    echo "Password protection: enabled"
else
    echo "Password protection: disabled"
fi

# Generate config.js for frontend
cat > /usr/share/nginx/html/assets/config.js << EOF
window.BINANCE_CONFIG = {
  apiKey: '$BINANCE_API_KEY',
  secretKey: '$BINANCE_SECRET_KEY',
  mode: '$MODE',
  password: '$PASSWORD'
};
EOF

echo "Configuration loaded from Home Assistant."

# Set environment variables for backend
export BINANCE_API_KEY
export BINANCE_SECRET_KEY
export MODE
export DB_PATH="/data/botdata.sqlite"

echo "Starting supervisor (nginx + Node.js backend)..."
echo "Backend will run in '$MODE' mode"
echo "Database will be stored in '/data'"

exec /usr/bin/supervisord -c /etc/supervisord.conf