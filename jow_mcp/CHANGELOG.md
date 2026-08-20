# Changelog

## 0.1.0

- Initial release.
- MCP server over streamable HTTP at `/mcp` with recipe read/search and
  menu/planning tools.
- Device-pairing link flow (`/pair` route + `pairing_token` option) exchanging
  a Jow pairing token for stored credentials; no password handled.
- Automatic access-token refresh on expiry / `401`, with credentials persisted
  in `/data`.
- Optional `server_token` bearer gate on the MCP endpoint.
