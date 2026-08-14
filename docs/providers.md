# Supported providers

CLIO Web Search works without a paid search provider. Each installation owns its provider
configuration and upstream usage.

## Web and technical search

SearXNG federates the configured engines and reports unavailable or rate-limited engines in
`unresponsive_engines`. A search is successful when the combined result set contains useful
results.

The bundled configuration includes:

- DuckDuckGo, Brave, Startpage, and Mojeek for general web discovery
- GitHub, Stack Overflow, Super User, and Ask Ubuntu for technical discovery
- arXiv, Crossref, DataCite, OpenAlex, PubMed, and Semantic Scholar for scholarly discovery

These are SearXNG engines, not separate tools exposed to the agent. Upstream services can apply
their own rate limits or anti-automation controls.

## Scholarly metadata and access

| Provider | Used for | Configuration |
| --- | --- | --- |
| Crossref | DOI metadata and scholarly search | Free; contact email recommended |
| DataCite | DOI fallback and scholarly search | Free; contact email recommended |
| OpenAlex | Scholarly search | Free API key optional and recommended |
| Unpaywall | Lawful open-access copy discovery | Contact email required |
| arXiv | Preprint discovery | No key |
| PubMed | Biomedical discovery | No key in the bundled configuration |
| Semantic Scholar | Scholarly discovery | No key in the bundled configuration |

Compose reads provider configuration from a local `.env` file. Create it from the tracked,
secret-free placeholder:

```bash
cp .env.example .env
```

Then set the providers you want to enable:

```dotenv
CLIO_WEB_SEARCH_CONTACT_EMAIL=you@example.org
CLIO_WEB_SEARCH_OPENALEX_API_KEY=
```

Without a contact email, search and document conversion continue to work and Unpaywall is reported
as disabled. CLIO Web Search does not bypass publisher access controls.

The [capabilities endpoint](api.md#capabilities) reports the active scholarly configuration.
