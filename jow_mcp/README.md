# Jow MCP

Search and read **[Jow](https://jow.fr)** recipes over the
[Model Context Protocol](https://modelcontextprotocol.io) so **Claude can find
and discuss Jow recipes**.

- Serves an MCP endpoint over HTTP at `http://<host>:8099/mcp` — add it to Claude
  as a custom connector.
- **Recipe search, full recipe detail, featured recipes and ingredient search
  work with no login** — ready the moment the add-on starts.
- Account features (your menu, collections, recommendations) need a Jow access
  token that jow.fr does not expose to the web; those tools error cleanly unless
  you inject a token yourself. See the **Documentation** tab.

See the **Documentation** tab for connecting Claude and the details on account
linking.

> Unofficial. Jow has no public API; this uses the same private
> `api.jow.fr/public` endpoints the jow.fr web app uses, for your own account.
