"""Async client for Jow's private ``api.jow.fr/public`` API.

Design notes
------------
Jow has no published API. Everything here was learned by observing the jow.fr
web app against ``https://api.jow.fr/public``:

* Reads that touch your account need ``Authorization: Bearer <accessToken>``.
* A ``x-jow-web-version`` header is expected on every call.
* Some reads are public (e.g. ``/recipes/featured``).
* Devices are linked with a short-lived *pairing token* obtained from a
  logged-in session (``GET /auth/pairing/code`` -> ``{code, pairingToken,
  expiresIn}``) and exchanged for a real token pair at ``POST /auth/clone``.

The exact request/response *shapes* of ``/auth/clone`` and of token refresh
could not be verified from the browser (CORS blocks those calls), so this
client tries a small ordered set of candidate shapes and remembers which one
worked, persisting the winner in the token file. If Jow changes the contract,
the strategies below are the single place to adjust.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("jow_mcp.client")


class JowAuthError(RuntimeError):
    """Raised when the client has no usable credentials and cannot get any."""


class JowAPIError(RuntimeError):
    """Raised when a Jow API call fails after auth handling."""

    def __init__(self, status: int, detail: Any, path: str) -> None:
        super().__init__(f"Jow API {status} on {path}: {detail!r}")
        self.status = status
        self.detail = detail
        self.path = path


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _decode_jwt_user_id(access_token: str) -> str | None:
    """Best-effort extraction of the account id from a JWT access token."""
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, binascii.Error):
        return None
    for key in ("userId", "user_id", "id", "_id", "sub", "uid"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    # Some tokens nest it, e.g. {"user": {"_id": ...}}
    user = payload.get("user")
    if isinstance(user, dict):
        for key in ("_id", "id", "userId"):
            if isinstance(user.get(key), str):
                return user[key]
    return None


def _jwt_expiry(access_token: str) -> float | None:
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, binascii.Error):
        return None
    exp = payload.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


class TokenStore:
    """Persists the token pair (+ learned strategies) as JSON on disk."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            self.data = {}
        except (ValueError, OSError) as exc:
            logger.warning("Could not read token file %s: %s", self.path, exc)
            self.data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.error("Could not persist token file %s: %s", self.path, exc)

    @property
    def access_token(self) -> str | None:
        return self.data.get("access_token")

    @property
    def refresh_token(self) -> str | None:
        return self.data.get("refresh_token")

    def set_tokens(self, access: str | None, refresh: str | None) -> None:
        if access:
            self.data["access_token"] = access
        if refresh:
            self.data["refresh_token"] = refresh
        self.data["updated_at"] = int(time.time())
        self.save()


# Candidate exchange strategies. Each entry: (name, method, path, body_builder,
# where body_builder(token) -> dict|None). Ordered most-likely first.
def _clone_strategies(token: str) -> list[dict[str, Any]]:
    return [
        {"name": "clone.pairingToken", "method": "POST", "path": "/auth/clone",
         "json": {"pairingToken": token}},
        {"name": "clone.token", "method": "POST", "path": "/auth/clone",
         "json": {"token": token}},
        {"name": "clone.code", "method": "POST", "path": "/auth/clone",
         "json": {"code": token}},
        {"name": "attach.pairingToken", "method": "POST", "path": "/auth/attach",
         "json": {"pairingToken": token}},
    ]


def _refresh_strategies(refresh_token: str) -> list[dict[str, Any]]:
    return [
        {"name": "clone.refresh", "method": "POST", "path": "/auth/clone",
         "json": {"refreshToken": refresh_token}},
        {"name": "auth.refresh", "method": "POST", "path": "/auth",
         "json": {"refreshToken": refresh_token, "grantType": "refresh_token"}},
        {"name": "auth.refresh_token", "method": "POST", "path": "/auth",
         "json": {"refresh_token": refresh_token, "grant_type": "refresh_token"}},
    ]


def _extract_tokens(payload: Any) -> tuple[str | None, str | None]:
    """Pull access/refresh tokens out of an arbitrary auth response body."""
    if not isinstance(payload, dict):
        return None, None
    # Common nestings: top-level, or under "tokens"/"auth"/"data".
    candidates = [payload]
    for key in ("tokens", "auth", "data", "session", "credentials"):
        if isinstance(payload.get(key), dict):
            candidates.append(payload[key])
    access = refresh = None
    for c in candidates:
        access = access or c.get("accessToken") or c.get("access_token") or c.get("token")
        refresh = refresh or c.get("refreshToken") or c.get("refresh_token")
    return access, refresh


class JowClient:
    def __init__(
        self,
        api_base: str,
        web_version: str,
        token_store: TokenStore,
        *,
        timeout: float = 20.0,
        locale: str = "fr-FR",
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.web_version = web_version
        self.tokens = token_store
        self.locale = locale
        self._http = httpx.AsyncClient(
            base_url=self.api_base,
            timeout=timeout,
            headers={
                "x-jow-web-version": web_version,
                "accept": "application/json",
                "accept-language": locale,
                "origin": "https://jow.fr",
                "referer": "https://jow.fr/",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ auth
    @property
    def is_authenticated(self) -> bool:
        return bool(self.tokens.access_token)

    @property
    def user_id(self) -> str | None:
        cached = self.tokens.data.get("user_id")
        if cached:
            return cached
        if self.tokens.access_token:
            uid = _decode_jwt_user_id(self.tokens.access_token)
            if uid:
                self.tokens.data["user_id"] = uid
                self.tokens.save()
            return uid
        return None

    async def pair(self, pairing_token: str) -> None:
        """Exchange a one-shot pairing token for a persistent token pair."""
        learned = self.tokens.data.get("clone_strategy")
        strategies = _clone_strategies(pairing_token)
        if learned:
            strategies.sort(key=lambda s: 0 if s["name"] == learned else 1)

        last_detail: Any = None
        for strat in strategies:
            try:
                resp = await self._http.request(
                    strat["method"], strat["path"], json=strat["json"]
                )
            except httpx.HTTPError as exc:
                last_detail = str(exc)
                continue
            if resp.status_code < 400:
                body = _safe_json(resp)
                access, refresh = _extract_tokens(body)
                if access:
                    self.tokens.set_tokens(access, refresh)
                    self.tokens.data["clone_strategy"] = strat["name"]
                    self.tokens.data.pop("user_id", None)
                    self.tokens.save()
                    logger.info("Paired successfully via %s", strat["name"])
                    return
                last_detail = f"{strat['name']} returned no token: {body!r}"
            else:
                last_detail = f"{strat['name']} -> {resp.status_code}: {_safe_json(resp)!r}"
            logger.debug("Pairing strategy failed: %s", last_detail)
        raise JowAuthError(
            "Could not exchange pairing token (it expires ~60s after "
            f"generation, so paste it quickly). Last error: {last_detail}"
        )

    async def _refresh(self) -> bool:
        refresh_token = self.tokens.refresh_token
        if not refresh_token:
            return False
        learned = self.tokens.data.get("refresh_strategy")
        strategies = _refresh_strategies(refresh_token)
        if learned:
            strategies.sort(key=lambda s: 0 if s["name"] == learned else 1)

        for strat in strategies:
            try:
                resp = await self._http.request(
                    strat["method"], strat["path"], json=strat["json"]
                )
            except httpx.HTTPError:
                continue
            if resp.status_code < 400:
                access, refresh = _extract_tokens(_safe_json(resp))
                if access:
                    self.tokens.set_tokens(access, refresh or refresh_token)
                    self.tokens.data["refresh_strategy"] = strat["name"]
                    self.tokens.data.pop("user_id", None)
                    self.tokens.save()
                    logger.info("Refreshed access token via %s", strat["name"])
                    return True
        logger.warning("Token refresh failed for all known strategies.")
        return False

    def _auth_headers(self) -> dict[str, str]:
        token = self.tokens.access_token
        return {"authorization": f"Bearer {token}"} if token else {}

    def _needs_refresh_soon(self) -> bool:
        token = self.tokens.access_token
        if not token:
            return False
        exp = _jwt_expiry(token)
        return exp is not None and exp - time.time() < 60

    # --------------------------------------------------------------- request
    async def request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Any:
        """Perform a request, refreshing the token on expiry / 401 once."""
        if auth and not self.is_authenticated:
            raise JowAuthError(
                "This action needs a linked Jow account. Provide a pairing "
                "token (see README) so the server can obtain credentials."
            )
        if auth and self._needs_refresh_soon():
            await self._refresh()

        headers = self._auth_headers() if auth else {}
        resp = await self._http.request(
            method, path, params=params, json=json_body, headers=headers
        )
        if auth and resp.status_code == 401:
            logger.info("Got 401 on %s; attempting token refresh.", path)
            if await self._refresh():
                resp = await self._http.request(
                    method, path, params=params, json=json_body,
                    headers=self._auth_headers(),
                )
        if resp.status_code >= 400:
            raise JowAPIError(resp.status_code, _safe_json(resp), path)
        return _safe_json(resp)

    # ------------------------------------------------------------- endpoints
    async def featured_recipes(self) -> Any:
        return await self.request("GET", "/recipes/featured", auth=False)

    async def search_recipes(self, query: str, limit: int = 20) -> Any:
        """Public recipe text search.

        Verified against the live API: the web app calls
        ``POST /recipe/quicksearch`` with the query in the *URL query string*
        (not the body) plus a ``supportsPaginatedSearch`` flag. The response is
        a ``{content: [...], links: {...}}`` envelope; we return ``content``.
        """
        params = {
            "supportsPaginatedSearch": "true",
            "query": query,
            "limit": limit,
        }
        result = await self.request(
            "POST", "/recipe/quicksearch", auth=False, params=params, json_body={}
        )
        if isinstance(result, dict) and "content" in result:
            return result["content"]
        return result

    async def get_recipe(self, recipe_id: str) -> Any:
        return await self.request("GET", f"/recipe/{recipe_id}", auth=False)

    async def recipe_feedbacks(self, recipe_id: str) -> Any:
        return await self.request("GET", f"/recipe/{recipe_id}/feedbacks", auth=False)

    async def quicksearch(self, query: str, limit: int = 8) -> Any:
        """Same public endpoint as search_recipes, smaller default limit."""
        return await self.search_recipes(query, limit=limit)

    async def recommendations(self) -> Any:
        return await self.request("GET", "/recipes/reco/main")

    async def search_ingredients(self, query: str) -> Any:
        return await self.request(
            "GET", "/ingredients/search", auth=False, params={"query": query}
        )

    # --- menu / planning ---
    async def get_menu(self) -> Any:
        return await self.request("GET", "/menu")

    async def get_menu_by_id(self, menu_id: str) -> Any:
        return await self.request("GET", f"/menu/{menu_id}")

    async def menu_basic_ingredients(self) -> Any:
        return await self.request("GET", "/menu/basic-ingredients")


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"_raw": resp.text[:500], "_status": resp.status_code}
