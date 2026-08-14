# Web MCP integration

The [CLIO Kit Web MCP](https://github.com/iowarp/clio-kit/tree/main/clio-kit-mcp-servers/web)
provides the agent-facing `search` and `fetch` tools. The provider is fixed when the MCP starts.

## Codex

```bash
codex mcp add web -- \
  uvx --from clio-kit==2.9.0 \
  clio-kit mcp-server web \
  -- \
  --provider searxng \
  --address http://127.0.0.1:8088
```

## Claude Code

```bash
claude mcp add web -- \
  uvx --from clio-kit==2.9.0 \
  clio-kit mcp-server web \
  -- \
  --provider searxng \
  --address http://127.0.0.1:8088
```

## MCP configuration

```json
{
  "mcpServers": {
    "web": {
      "command": "uvx",
      "args": [
        "--from",
        "clio-kit==2.9.0",
        "clio-kit",
        "mcp-server",
        "web",
        "--",
        "--provider",
        "searxng",
        "--address",
        "http://127.0.0.1:8088"
      ]
    }
  }
}
```

Replace the address with the trusted LAN endpoint when the service runs on another machine.

## Tool behavior

- `search` exposes the selectors supported by the installed SearXNG provider and preserves
  scholarly result metadata when available.
- `fetch` accepts an HTTP(S) URL or DOI. It reads HTML and text locally and sends supported
  documents to CLIO Web Search.
- Long conversions return a durable conversion ID and retry interval. Repeating the fetch resumes
  the content-addressed job.

The Web MCP capability resource, `web://capabilities`, describes the active installation.
