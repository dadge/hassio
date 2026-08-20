"""Jow MCP server.

Exposes Jow recipe and meal-planning reads as MCP tools over streamable HTTP
so Claude can connect to it as a custom connector. Also serves two plain HTTP
routes used for setup and monitoring:

* ``GET  /health``  -> liveness + whether an account is linked
* ``POST /pair``    -> exchange a pairing token for stored credentials
                       (body: {"pairingToken": "v1:..."} )
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import Config
from .jow_client import JowAPIError, JowAuthError, JowClient, TokenStore

logger = logging.getLogger("jow_mcp.server")

# Populated in build_server(); module-level so tool functions can reach it.
_config: Config
_client: JowClient

STATIC_BASE = "https://static.jow.fr"

mcp = FastMCP("jow")


# --------------------------------------------------------------------- tools
@mcp.tool()
async def jow_auth_status() -> dict[str, Any]:
    """Report whether a Jow account is linked and its user id, if known.

    Call this first if other tools fail with an auth error: it tells you
    whether the server still needs to be paired to a Jow account.
    """
    return {
        "linked": _client.is_authenticated,
        "user_id": _client.user_id,
        "api_base": _config.api_base,
        "note": (
            "Linked and ready." if _client.is_authenticated else
            "Not linked. Generate a pairing token from a logged-in jow.fr "
            "session and POST it to /pair, or set JOW_PAIRING_TOKEN."
        ),
    }


@mcp.tool()
async def jow_pair(pairing_token: str) -> dict[str, Any]:
    """Link this server to your Jow account using a one-shot pairing token.

    Generate the token from a browser logged in to jow.fr (see README), then
    pass it here. It expires ~60 seconds after generation, so pair promptly.
    """
    await _client.pair(pairing_token.strip())
    return {"linked": True, "user_id": _client.user_id}


@mcp.tool()
async def jow_featured_recipes() -> Any:
    """Get Jow's current featured/front-page recipes. No account required."""
    return _enrich(await _client.featured_recipes())


@mcp.tool()
async def jow_search_recipes(query: str, limit: int = 20) -> Any:
    """Search Jow recipes by free text (e.g. 'poulet curry', 'gâteau chocolat').

    Returns matching recipes with ids, titles, timings and ingredients.
    """
    return _enrich(await _client.search_recipes(query, limit=limit))


@mcp.tool()
async def jow_quicksearch(query: str) -> Any:
    """Fast lightweight recipe search (autocomplete-style). No account required."""
    return _enrich(await _client.quicksearch(query))


@mcp.tool()
async def jow_get_recipe(recipe_id: str) -> Any:
    """Get one recipe's full detail (ingredients, steps, timings) by its id.

    The id is the value found in search results, not the URL slug.
    """
    return _enrich(await _client.get_recipe(recipe_id))


@mcp.tool()
async def jow_recipe_recommendations() -> Any:
    """Get personalised recipe recommendations for the linked account."""
    return _enrich(await _client.recommendations())


@mcp.tool()
async def jow_search_ingredients(query: str) -> Any:
    """Search Jow's ingredient catalogue by name. No account required."""
    return await _client.search_ingredients(query)


@mcp.tool()
async def jow_get_menu() -> Any:
    """Get the linked account's current weekly menu / meal plan."""
    return _enrich(await _client.get_menu())


@mcp.tool()
async def jow_get_menu_by_id(menu_id: str) -> Any:
    """Get a specific menu / meal plan by its id."""
    return _enrich(await _client.get_menu_by_id(menu_id))


@mcp.tool()
async def jow_menu_basic_ingredients() -> Any:
    """Get the 'basic ingredients' (pantry staples) list used by meal planning."""
    return await _client.menu_basic_ingredients()


def _enrich(payload: Any) -> Any:
    """Rewrite relative Jow image paths to absolute URLs, recursively.

    Jow returns image references like ``recipes/xxxx.png``; prefixing the CDN
    base makes them directly usable by the client.
    """
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "imageUrl" and isinstance(v, str) and v and not v.startswith("http"):
                    out[k] = f"{STATIC_BASE}/{v.lstrip('/')}"
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(payload)


# ------------------------------------------------------------- HTTP routes
@mcp.custom_route("/health", methods=["GET"])
async def health(_req: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "linked": _client.is_authenticated})


@mcp.custom_route("/pair", methods=["POST"])
async def pair_route(req: Request) -> JSONResponse:
    try:
        body = await req.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    token = (body.get("pairingToken") or body.get("token") or "").strip()
    if not token:
        return JSONResponse(
            {"error": "Provide a pairing token as {\"pairingToken\": \"v1:...\"}"},
            status_code=400,
        )
    try:
        await _client.pair(token)
    except JowAuthError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"linked": True, "user_id": _client.user_id})


# ------------------------------------------------------------- app assembly
def build_server(config: Config) -> FastMCP:
    global _config, _client
    _config = config
    store = TokenStore(config.token_path)
    _client = JowClient(
        api_base=config.api_base,
        web_version=config.web_version,
        token_store=store,
        timeout=config.request_timeout,
        locale=config.locale,
    )
    mcp.settings.host = config.host
    mcp.settings.port = config.port
    return mcp


def _install_token_gate(app, server_token: str) -> None:
    """Require ``Authorization: Bearer <server_token>`` on all but /health."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class Gate(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path == "/health":
                return await call_next(request)
            header = request.headers.get("authorization", "")
            if header != f"Bearer {server_token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app.add_middleware(Gate)


def run() -> None:
    import uvicorn

    config = Config.from_env()
    # Home Assistant offers log levels (trace/notice/fatal) that neither the
    # stdlib logging module nor uvicorn accept; normalise to the nearest valid
    # value for each.
    py_level = {"TRACE": logging.DEBUG, "NOTICE": logging.INFO,
                "FATAL": logging.CRITICAL}.get(
        config.log_level, getattr(logging, config.log_level, logging.INFO))
    uvicorn_level = {"trace": "debug", "notice": "info", "fatal": "critical",
                     "warn": "warning"}.get(
        config.log_level.lower(), config.log_level.lower())
    if uvicorn_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
        uvicorn_level = "info"
    logging.basicConfig(
        level=py_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = build_server(config)

    app = server.streamable_http_app()

    # Bootstrap pairing on startup if a token was supplied via env.
    if config.pairing_token and not _client.is_authenticated:
        _schedule_bootstrap(app, config.pairing_token)

    if config.server_token:
        _install_token_gate(app, config.server_token)
        logger.info("MCP endpoint protected by JOW_SERVER_TOKEN.")

    logger.info(
        "Jow MCP listening on http://%s:%s  (MCP at /mcp, setup at /pair, /health)",
        config.host, config.port,
    )
    uvicorn.run(app, host=config.host, port=config.port, log_level=uvicorn_level)


def _schedule_bootstrap(app, pairing_token: str) -> None:
    """Attempt the initial pairing exchange during app startup."""
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app_):
        async with original_lifespan(app_):
            try:
                await _client.pair(pairing_token)
                logger.info("Initial pairing from JOW_PAIRING_TOKEN succeeded.")
            except JowAuthError as exc:
                logger.warning("Initial pairing failed: %s", exc)
            yield

    app.router.lifespan_context = lifespan


if __name__ == "__main__":
    run()
