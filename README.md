# CLIO Web Search

![CLIO Web Search — self-hosted web search and document understanding for AI agents](docs/assets/clio-web-search-banner.png)

Self-hosted web search and document understanding for AI agents.

We are building [CLIO Agent](https://github.com/iowarp/clio-agent), an SDK for creating
scientific agents. Scientific work requires agents to discover technical information, retrieve
many kinds of documents, process scientific papers, preserve scholarly metadata, and provide
traceable sources for their conclusions.

Powerful libraries already address individual parts of this problem.
[SearXNG](https://github.com/searxng/searxng) provides private, federated web search.
[Docling](https://github.com/docling-project/docling) converts complex documents into structured
representations. [GROBID](https://github.com/kermitt2/grobid) extracts scholarly metadata,
references, and citation contexts from scientific papers. CLIO Web Search brings these
capabilities together as a unified, self-hosted service designed for agent use.

CLIO Web Search is designed to work with the
[CLIO Kit Web MCP](https://github.com/iowarp/clio-kit/tree/main/clio-kit-mcp-servers/web). The Web
MCP gives agents a small interface built around `search` and `fetch`, while CLIO Web Search
supplies the search, document conversion, DOI resolution, and scientific-paper processing behind
those tools.

The container also bundles Valkey for the Web MCP's FastMCP/Docket task state. A locally installed
Web MCP needs only this service's HTTP URL: it discovers the Valkey endpoint and an isolated queue
namespace automatically. The MCP task remains agent-local; document conversion remains durable in
this service.

The Web MCP is part of [CLIO Kit](https://github.com/iowarp/clio-kit), a growing suite of
scientific tools for agents. CLIO Web Search is distributed as an independent container and can
be used by any compatible MCP client or agent system.

An agent can search the web, fetch a URL or DOI, and receive readable Markdown together with
structured metadata, references, and in-text citation contexts.

## Deploy directly

```bash
docker run --detach \
  --name clio-web-search \
  --restart unless-stopped \
  --publish 127.0.0.1:8089:8080 \
  --publish 127.0.0.1:8090:6379 \
  --env CLIO_WEB_SEARCH_CONTACT_EMAIL=you@example.org \
  --volume clio-web-search-data:/var/lib/clio-web-search \
  ghcr.io/iowarp/clio-web-search:0.3.0
```

Docker pulls the published image automatically. A Git checkout is not required.
Container health remains in `starting` until Docling's PDF pipeline has been loaded and warmed.
Conversions have no overall elapsed-time deadline; only bounded upstream network requests time out.

## Or deploy with Compose

```bash
git clone https://github.com/iowarp/clio-web-search.git
cd clio-web-search
docker compose up -d
```

The defaults bind HTTP to `127.0.0.1:8089` and Valkey to `127.0.0.1:8090`. Copy `.env.example` to `.env` to customize the
deployment.

## Connect the Web MCP

```bash
uvx --from clio-kit==2.10.0 \
  clio-kit mcp-server web \
  -- \
  --remote-url http://127.0.0.1:8089
```

## Documentation

- [Deployment](docs/deployment.md)
- [Other container platforms](docs/other-platforms.md)
- [Supported providers](docs/providers.md)
- [Security model](docs/security_model.md)
- [Web MCP integration](docs/web-mcp.md)
- [FastMCP task infrastructure](docs/task-backend.md)
- [HTTP API](docs/api.md)

### LLMs start here

The [AI integration guide](docs/ai/README.md) is the entry point for agents, coding assistants, and
automated repository tools.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pyright
uv run pytest
```

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for bundled components and licenses.
