# FastMCP task infrastructure

CLIO Web Search bundles Valkey as the durable Docket backend for the CLIO Kit Web MCP. The stores
have separate responsibilities:

- SQLite owns conversion admission, progress events, cancellation, and results.
- Valkey owns FastMCP task identity, status, progress snapshots, and cancellation delivery.

The normal topology is `agent -> local Web MCP -> CLIO Web Search`. Start the Web MCP with the
single HTTP `--remote-url`; it reads `/v1/capabilities`, creates or reuses its local agent ID, calls
`/v1/task-backend/session`, and configures Docket from the returned Valkey endpoint and queue name.
The Valkey port defaults to the HTTP port plus one (`8089` and `8090` in Compose), but discovery is
authoritative when either port is changed.

`fetch` requires the negotiated `io.modelcontextprotocol/tasks` extension. `tasks/get` exposes the
latest progress message; `fetch_events` queries the complete ordered backend event log by
conversion ID. Cancelling the native MCP task calls the backend cancellation endpoint before the
task becomes terminal.

In trusted mode, publish both ports only on a trusted LAN or Tailscale interface. In secure mode,
configure an API bearer token and TLS certificate paths; discovery then provisions an ACL identity
limited to one stable Docket queue namespace and returns a `rediss://` connection contract.
