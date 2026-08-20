"""Configuration loading for the Jow MCP server.

All settings come from environment variables so the same code runs identically
as a local process, a Docker container, or a Home Assistant add-on (the add-on
`run.sh` maps user options to these env vars via bashio).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # --- Jow API ---
    api_base: str = "https://api.jow.fr/public"
    # Sent as x-jow-web-version. Jow's API rejects/ignores calls without a
    # plausible client version header. Bump if Jow starts enforcing a minimum.
    web_version: str = "5.0.0"

    # --- Token storage ---
    # Where the access/refresh token pair is persisted between restarts.
    # In the HA add-on this points at the persistent /data volume.
    token_path: str = "/data/jow_tokens.json"

    # --- Bootstrap pairing token ---
    # A one-shot pairing token (v1:code:ts:hash) copied from a logged-in
    # jow.fr browser session. On first start the server exchanges it for a
    # real access/refresh token pair via /auth/clone, then clears it.
    pairing_token: str | None = None

    # --- HTTP transport ---
    host: str = "0.0.0.0"
    port: int = 8099
    # Optional bearer secret protecting the MCP endpoint so only clients that
    # know it (i.e. your Claude connector) can talk to the server on your LAN.
    server_token: str | None = None

    # --- Behaviour ---
    request_timeout: float = 20.0
    locale: str = "fr-FR"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_base=os.environ.get("JOW_API_BASE", cls.api_base).rstrip("/"),
            web_version=os.environ.get("JOW_WEB_VERSION", cls.web_version),
            token_path=os.environ.get("JOW_TOKEN_PATH", cls.token_path),
            pairing_token=os.environ.get("JOW_PAIRING_TOKEN") or None,
            host=os.environ.get("JOW_HOST", cls.host),
            port=int(os.environ.get("JOW_PORT", str(cls.port))),
            server_token=os.environ.get("JOW_SERVER_TOKEN") or None,
            request_timeout=float(os.environ.get("JOW_TIMEOUT", str(cls.request_timeout))),
            locale=os.environ.get("JOW_LOCALE", cls.locale),
            log_level=os.environ.get("JOW_LOG_LEVEL", cls.log_level).upper(),
        )
