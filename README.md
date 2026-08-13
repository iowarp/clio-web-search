# CLIO Search

CLIO Search is an optional, self-hosted search and document-enrichment backend
for the CLIO Kit Web MCP. It packages SearXNG, Docling, GROBID CRF, DOI metadata
resolution, and a durable document-conversion queue behind one HTTP origin.

It is distributed software, not a hosted IOWarp service. Every installation owns
its endpoint, provider identity, optional keys, cache, and network exposure.

## Run

```bash
docker run --detach --name clio-search --restart unless-stopped \
  --publish 127.0.0.1:8088:8080 \
  --env CLIO_SEARCH_CONTACT_EMAIL=you@example.org \
  --volume clio-search-data:/var/lib/clio-search \
  ghcr.io/iowarp/clio-search:0.1.0
```

Bind an explicit LAN address instead of `127.0.0.1` only when other trusted
machines need access. CLIO Search has no Internet-facing authentication layer.
The container entrypoint repairs ownership on an existing data volume and drops
permanently to UID/GID 65534 before it starts SearXNG, GROBID, or the gateway.

The contact email is optional and has no built-in default. Without it, ordinary
search and document conversion remain available, while Unpaywall enrichment is
reported as disabled.

## Web MCP

```bash
uvx --from clio-kit clio-kit mcp-server web \
  -- \
  --provider searxng \
  --address http://127.0.0.1:8088
```

The provider is selected when the MCP starts. It is not an agent-visible search
parameter. Install a second named MCP server if an agent needs another provider.

## API

- `GET /healthz` — gateway liveness.
- `GET /readyz` — SearXNG and durable queue readiness.
- `GET /v1/capabilities` — enabled search, document, and scholarly features.
- `GET|POST /search` — SearXNG-compatible search endpoint.
- `POST /v1/doi/resolve` — normalized metadata and lawful access candidates.
- `POST /v1/documents` — content-addressed document submission.
- `GET /v1/documents/{id}` — durable conversion status or result.

Document results contain Markdown plus Docling structure. Scholarly PDFs can add
GROBID header metadata, bibliography entries, and in-text citation contexts.
Citation traversal remains the research agent's responsibility.

## Configuration

All settings use the `CLIO_SEARCH_` prefix. Important fields are
`CONTACT_EMAIL`, `OPENALEX_API_KEY`, `DATA_DIR`, `MAX_INPUT_BYTES`, `WORKERS`,
`MAX_PENDING_JOBS`, `CACHE_TTL_DAYS`, and `CACHE_MAX_BYTES`.

The default queue has one worker and 32 pending slots. Its SQLite state and
content-addressed objects survive restarts in `/var/lib/clio-search`. Valkey is
not used or required.

## Verification

```bash
curl --fail http://127.0.0.1:8088/healthz
curl --fail http://127.0.0.1:8088/readyz
curl --get http://127.0.0.1:8088/search \
  --data-urlencode 'q=exact attention' \
  --data 'format=json&categories=science'
```

`/readyz` is ready only when the gateway, SearXNG, GROBID, and persistent queue
are available. A search remains successful when it has useful results; any
secondary engine rate limits or CAPTCHA responses remain visible in SearXNG's
`unresponsive_engines` field.

The release workflow builds `linux/amd64` and `linux/arm64` images. Each release
is pinned by its immutable image digest; use that digest when reproducibility is
more important than a movable semantic-version tag.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pyright
uv run pytest
```

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the major bundled
components and their pinned versions.
