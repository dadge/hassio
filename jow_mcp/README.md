# Jow MCP

Read **[Jow](https://jow.fr)** recipes and meal plans over the
[Model Context Protocol](https://modelcontextprotocol.io), linked to your own
Jow account, so **Claude can search and discuss your Jow**.

- Serves an MCP endpoint over HTTP at `http://<host>:8099/mcp` — add it to Claude
  as a custom connector.
- Links to your account with Jow's own **device-pairing** flow — no password is
  ever handled or stored.
- **Auto-refreshes** its access token; credentials persist in `/data` across
  restarts and updates.

See the **Documentation** tab for setup: minting a pairing token, linking, and
connecting Claude.

> Unofficial. Jow has no public API; this uses the same private
> `api.jow.fr/public` endpoints the jow.fr web app uses, for your own account.
