# Web MCP integration

The [CLIO Kit Web MCP](https://github.com/iowarp/clio-kit/tree/main/clio-kit-mcp-servers/web)
provides the agent-facing `search` and `fetch` tools. The provider is fixed when the MCP starts.

## Codex

```bash
codex mcp add web -- \
  uvx --from clio-kit==2.10.0 \
  clio-kit mcp-server web \
  -- \
  --remote-url http://127.0.0.1:8089
```

## Claude Code

```bash
claude mcp add web -- \
  uvx --from clio-kit==2.10.0 \
  clio-kit mcp-server web \
  -- \
  --remote-url http://127.0.0.1:8089
```

## MCP configuration

```json
{
  "mcpServers": {
    "web": {
      "command": "uvx",
      "args": [
        "--from",
        "clio-kit==2.10.0",
        "clio-kit",
        "mcp-server",
        "web",
        "--",
        "--remote-url",
        "http://127.0.0.1:8089"
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
- `fetch` runs as a native MCP task until its conversion completes or the agent cancels it.
- `tasks/get` exposes the latest progress message and `fetch_events` returns the full ordered log.

The Web MCP capability resource, `web://capabilities`, describes the active installation.

## Native fetch tasks

The CLIO Kit Web MCP remains the only combined `search` and `fetch` MCP interface. Its `fetch` tool
uses FastMCP tasks and discovers the bundled Valkey service from the CLIO Web Search HTTP URL.
CLIO Web Search does not expose a duplicate document-conversion MCP. See
[FastMCP task infrastructure](task-backend.md).
