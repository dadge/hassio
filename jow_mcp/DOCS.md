# Jow MCP — Documentation

An MCP server exposing Jow recipes to Claude. **Recipe search and reads work
out of the box, no login.** Account-specific data (your menu, collections,
recommendations) needs a Jow access token, which — as explained below — jow.fr
does not make available to a web client, so those tools are best-effort.

## Tools exposed

Over streamable HTTP at `/mcp`:

| Tool | Login? | Description |
|------|:--:|------|
| `jow_auth_status` | – | Is an account linked? Reminds you what needs it |
| `jow_search_recipes` | **no** | Full-text recipe search (query, limit) |
| `jow_quicksearch` | **no** | Same, fewer results |
| `jow_get_recipe` | **no** | One recipe's full detail (ingredients, steps) by id |
| `jow_featured_recipes` | **no** | Front-page featured recipes |
| `jow_search_ingredients` | **no** | Ingredient catalogue search |
| `jow_recipe_recommendations` | yes | Personalised recommendations |
| `jow_get_menu` | yes | Your weekly menu / meal plan |
| `jow_get_menu_by_id` | yes | A specific menu by id |
| `jow_menu_basic_ingredients` | yes | Pantry-staples list |
| `jow_pair` | – | Attempt to link with a token pair (see below) |

Plain HTTP routes: `GET /health`, `POST /pair`.

The five **no-login** tools cover the main use case: search Jow, pull a recipe's
full ingredients and steps, and discuss them with Claude. They work the moment
the add-on starts.

## Connect Claude

The endpoint is `http://<HA-IP>:8099/mcp`.

**Claude Code**

```bash
claude mcp add --transport http jow http://<HA-IP>:8099/mcp
```

With a `server_token` set:

```bash
claude mcp add --transport http jow http://<HA-IP>:8099/mcp \
  --header "Authorization: Bearer <server_token>"
```

**claude.ai / Claude Desktop** — add a **Custom Connector** for the same `/mcp`
URL (with the `Authorization` header if you set a `server_token`). Claude must
reach the endpoint; for access beyond your LAN, put it behind your existing
reverse proxy or VPN rather than exposing the port to the internet.

Then ask, e.g. *"search Jow for a quick chicken curry and show me the recipe"*.

Sanity-check first:

```bash
Invoke-RestMethod "http://<HA-IP>:8099/health"   # PowerShell
curl.exe http://<HA-IP>:8099/health              # curl
```

## Configuration

| Option | Description |
|--------|------|
| `server_token` | If set, clients must send `Authorization: Bearer <server_token>` to reach `/mcp`. `/health` stays open. Recommended so nothing else on your LAN can use the endpoint. |
| `web_version` | Sent as `x-jow-web-version`. Change only if Jow starts rejecting the default. |
| `log_level` | Add-on log verbosity. |
| `pairing_token` | Only relevant if account linking becomes possible for you — see below. |

## About account linking (the honest version)

The original goal was to link the add-on to your Jow account so it could read
your menu and collections. After reverse-engineering the API, that turns out
**not to be reachable from the web**:

- Jow's data endpoints authenticate with a **Bearer access token**, not a
  cookie.
- The jow.fr web app keeps that token **server-side / obfuscated** and never
  sends it from the browser on normal pages, so it can't be copied from
  DevTools.
- Jow's device-pairing flow (`/auth/pairing/code` → `/auth/clone`) needs an
  authenticated **`/auth/attach`** step to authorise the new device, and that
  step requires the very Bearer token above. jow.fr exposes no web UI to
  perform it.

So there is no web-only way to hand the add-on your account credentials. **If**
you can obtain a Jow **access token + refresh token** by other means (e.g. by
intercepting the Jow mobile app's traffic against `api.jow.fr/public`), you can
inject them:

```bash
curl.exe -X POST http://<HA-IP>:8099/pair \
  -H "content-type: application/json" \
  --data-raw '{\"accessToken\":\"...\",\"refreshToken\":\"...\"}'
```

The add-on will store them and auto-refresh. Without them, the four account
tools return a clear "needs a linked account" error and everything else works.

> Windows PowerShell note: `curl` there is an alias for `Invoke-WebRequest` and
> mangles JSON. Use `curl.exe` with escaped quotes as above, or
> `Invoke-RestMethod -Uri ... -Method Post -ContentType application/json -Body '...'`.

## Robustness note

Recipe search, get-recipe, featured and ingredient search are verified against
the live public API. The token-refresh and `/auth/clone` request shapes could
not be verified (they're not reachable without a token), so the client tries a
small ordered set of candidate shapes and persists whichever works, logging its
choice. Adjust the strategy lists in `jow_client.py` if Jow changes them.
