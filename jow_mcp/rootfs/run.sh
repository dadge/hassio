#!/usr/bin/env bash
# ==============================================================================
# Jow MCP — Home Assistant add-on entrypoint
#
#   1. Read add-on options from /data/options.json (jq; no bashio on this base)
#   2. Map them to the JOW_* env vars the server reads
#   3. Use a one-shot pairing_token only while no credentials are stored yet,
#      so a stale token in the config can't fight the persisted session
#   4. exec the MCP server, which serves /mcp, /pair and /health on :8099
# ==============================================================================
set -Eeuo pipefail

OPTIONS_FILE=/data/options.json

log()   { printf '%s [%-5s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "${*:2}" >&2; }
info()  { log INFO "$@"; }
warn()  { log WARN "$@"; }

opt() { jq -r --arg k "$1" 'if .[$k] == null then "" else (.[$k] | tostring) end' "$OPTIONS_FILE"; }

export JOW_TOKEN_PATH="/data/jow_tokens.json"
export JOW_HOST="0.0.0.0"
export JOW_PORT="8099"

if [[ -f "$OPTIONS_FILE" ]]; then
    LOG_LEVEL="$(opt log_level)";      [[ -n "$LOG_LEVEL" ]]   && export JOW_LOG_LEVEL="$LOG_LEVEL"
    WEB_VERSION="$(opt web_version)";  [[ -n "$WEB_VERSION" ]] && export JOW_WEB_VERSION="$WEB_VERSION"
    SERVER_TOKEN="$(opt server_token)";[[ -n "$SERVER_TOKEN" ]]&& export JOW_SERVER_TOKEN="$SERVER_TOKEN"

    PAIRING_TOKEN="$(opt pairing_token)"
    if [[ -n "$PAIRING_TOKEN" ]]; then
        if [[ ! -f "$JOW_TOKEN_PATH" ]]; then
            export JOW_PAIRING_TOKEN="$PAIRING_TOKEN"
            info "Pairing token supplied; will link to your Jow account on startup."
        else
            info "Existing credentials found in /data; ignoring config pairing_token."
        fi
    fi
else
    warn "Options file $OPTIONS_FILE not found — starting with defaults."
fi

if [[ -n "${JOW_SERVER_TOKEN:-}" ]]; then
    info "MCP endpoint protected by server_token."
fi

info "Starting Jow MCP server on port ${JOW_PORT} (MCP at /mcp, setup at /pair)."
exec python3 -m jow_mcp.server
