# LLMs start here

CLIO Web Search gives AI agents one self-hosted backend for web discovery, DOI resolution, and
document understanding. It is normally accessed through the
[CLIO Kit Web MCP](https://github.com/iowarp/clio-kit/tree/main/clio-kit-mcp-servers/web), which
exposes `search` and `fetch`.

## Mental model

1. `search` discovers web, technical, or scholarly sources through SearXNG.
2. `fetch` retrieves a URL or DOI.
3. HTML and text are read directly by the Web MCP.
4. PDFs and structured documents are submitted to CLIO Web Search.
5. Docling returns Markdown and document structure; GROBID adds scholarly metadata and citations
   when applicable.

The agent chooses what to search, read, cite, and follow. CLIO Web Search supplies retrieval and
document structure; it does not perform research synthesis or citation traversal on the agent's
behalf.

## Integration rules

- Treat `unresponsive_engines` as diagnostics when the search still returned useful results.
- Preserve result URLs and provider metadata in citations.
- When `fetch` reports `document_conversion_pending`, retry after the supplied interval using the
  same target.
- Do not replace missing document content with search snippets.
- Use the returned document metadata, references, and citation contexts as evidence, then fetch any
  additional sources needed for the task.
- Read `web://capabilities` before assuming optional scholarly enrichment is enabled.

## Documentation map

- [Deployment](../deployment.md)
- [Other container platforms](../other-platforms.md)
- [Supported providers](../providers.md)
- [Security model](../security_model.md)
- [Web MCP integration](../web-mcp.md)
- [HTTP API](../api.md)
