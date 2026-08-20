# Changelog

## 0.1.1

- **Recipe search now works without any login.** Fixed `jow_search_recipes` /
  `jow_quicksearch` to call the verified public `POST /recipe/quicksearch`
  endpoint (query in the URL query string + `supportsPaginatedSearch`), instead
  of the auth-only `/recipes/search`.
- Documented that account linking (menu/collections/recommendations) is **not
  reachable from the web** — Jow keeps the access token server-side and the
  device-pairing `attach` step needs it. Those tools now error cleanly.
- `/pair` route additionally accepts a direct `{accessToken, refreshToken}`
  pair for users who obtain a token by other means (e.g. the mobile app).
- `jow_auth_status` explains what works without linking.

## 0.1.0

- Initial release.
- MCP server over streamable HTTP at `/mcp` with recipe read/search and
  menu/planning tools.
- Device-pairing link flow (`/pair` route + `pairing_token` option) exchanging
  a Jow pairing token for stored credentials; no password handled.
- Automatic access-token refresh on expiry / `401`, with credentials persisted
  in `/data`.
- Optional `server_token` bearer gate on the MCP endpoint.
