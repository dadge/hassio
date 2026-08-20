# Jow MCP — Documentation

An MCP server exposing your Jow recipes and meal plans to Claude. It links to
your account through Jow's device-pairing mechanism and refreshes its token
automatically.

## Tools exposed

Over streamable HTTP at `/mcp`:

| Tool | Account? | Description |
|------|:--:|------|
| `jow_auth_status` | – | Is the server linked? What's the user id? |
| `jow_pair` | – | Link the server with a pairing token |
| `jow_featured_recipes` | no | Front-page featured recipes |
| `jow_search_recipes` | yes | Full-text recipe search |
| `jow_quicksearch` | no | Lightweight autocomplete-style search |
| `jow_get_recipe` | no | One recipe's full detail by id |
| `jow_recipe_recommendations` | yes | Personalised recommendations |
| `jow_search_ingredients` | no | Ingredient catalogue search |
| `jow_get_menu` | yes | Your current weekly menu / meal plan |
| `jow_get_menu_by_id` | yes | A specific menu by id |
| `jow_menu_basic_ingredients` | yes | Pantry-staples list |

Plain HTTP routes: `GET /health`, `POST /pair`.

## Configuration

| Option | Description |
|--------|------|
| `pairing_token` | One-shot pairing token (`v1:...`). **Prefer the `/pair` route** — the token expires ~60 s after minting, too fast for a config save + restart. Only used while `/data` has no stored credentials. |
| `server_token` | If set, clients must send `Authorization: Bearer <server_token>` to reach `/mcp`. Leave empty to allow any client that can reach the port. `/health` stays open. |
| `web_version` | Sent as `x-jow-web-version`. Bump only if Jow starts rejecting the default. |
| `log_level` | Add-on log verbosity. |

## Linking to your Jow account

Jow's real login is phone/OTP based and can't be automated headlessly, so this
add-on uses Jow's **device-pairing** flow instead: a logged-in browser mints a
short-lived token, and the add-on exchanges it for its own credentials.

### 1. Mint a pairing token

Log in at <https://jow.fr>, open the browser devtools **Console**, and run:

```js
fetch('https://api.jow.fr/public/auth/pairing/code', {
  credentials: 'include',
  headers: { 'x-jow-web-version': '5.0.0' },
}).then(r => r.json()).then(d => console.log('PAIRING TOKEN:\n' + d.pairingToken));
```

Copy the printed `v1:...` value.

### 2. Hand it to the add-on (within ~60 s)

Start the add-on, then:

```bash
curl -X POST http://<HA-IP>:8099/pair \
  -H 'content-type: application/json' \
  -d '{"pairingToken":"v1:PASTE_HERE"}'
```

If you set a `server_token`, add `-H "Authorization: Bearer <server_token>"`.

A `{"linked": true, "user_id": "..."}` response means you're done. Check any
time with `curl http://<HA-IP>:8099/health`.

Credentials are stored in `/data/jow_tokens.json` and reused after restarts; the
add-on refreshes the access token automatically when it nears expiry or a call
returns `401`.

## Connect Claude

The endpoint is `http://<HA-IP>:8099/mcp`.

**Claude Code**

```bash
claude mcp add --transport http jow http://<HA-IP>:8099/mcp
```

With a `server_token`:

```bash
claude mcp add --transport http jow http://<HA-IP>:8099/mcp \
  --header "Authorization: Bearer <server_token>"
```

**claude.ai / Claude Desktop** — add a **Custom Connector** for the same `/mcp`
URL (with the `Authorization` header if you set a `server_token`). Claude must
be able to reach the endpoint; for access from outside your LAN put it behind
your existing reverse proxy or VPN rather than exposing the port to the internet.

Then ask, e.g. *"search my Jow for a quick chicken recipe"* or *"what's on my Jow
menu this week?"*.

## Robustness note

The featured-recipes read and the pairing-token *mint* are verified against the
live API. The `/auth/clone` exchange and the token-refresh request shapes could
not be verified from a browser (CORS blocks them; they run fine server-side), so
the client tries a small ordered set of candidate request shapes for pairing,
refresh and search and **persists whichever one works** into the token file. If
pairing or refresh fails, the add-on log names the strategy it tried; the shapes
live in `jow_client.py` (`_clone_strategies` / `_refresh_strategies`).

## Troubleshooting

- **`jow_search_recipes` etc. error with "needs a linked Jow account"** — run
  `jow_auth_status` / `curl .../health`; if not linked, redo the pairing steps.
- **Pairing returns an error** — the token likely expired (mint and `curl`
  back-to-back), or the exchange shape changed (check the log).
- **Claude can't connect** — confirm the port is reachable
  (`curl http://<HA-IP>:8099/health`) and that the `Authorization` header matches
  `server_token` if you set one.
