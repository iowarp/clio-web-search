# Deployment

CLIO Web Search is distributed as a Linux container for `amd64` and `arm64` hosts. The image
contains SearXNG, Docling, GROBID, their models, and the HTTP gateway.

## Docker

`docker run` pulls the published image directly from GHCR. It does not require Git.

```bash
docker run --detach \
  --name clio-web-search \
  --restart unless-stopped \
  --publish 127.0.0.1:8088:8080 \
  --env CLIO_WEB_SEARCH_CONTACT_EMAIL=you@example.org \
  --volume clio-web-search-data:/var/lib/clio-web-search \
  ghcr.io/iowarp/clio-web-search:0.2.0
```

Omit `CLIO_WEB_SEARCH_CONTACT_EMAIL` if Unpaywall enrichment is not needed. Use a release digest in
place of `:0.2.0` for an immutable deployment.

## Docker Compose

The repository includes an annotated `compose.yaml` with safe defaults.

```bash
git clone https://github.com/iowarp/clio-web-search.git
cd clio-web-search
docker compose up -d
```

To customize it:

```bash
cp .env.example .env
docker compose up -d
```

Compose reads `.env` automatically. Empty optional values remain disabled.

## Configuration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `CLIO_WEB_SEARCH_IMAGE` | `ghcr.io/iowarp/clio-web-search:0.2.0` | Compose image tag or digest |
| `CLIO_WEB_SEARCH_BIND_ADDRESS` | `127.0.0.1` | Host address published by Compose |
| `CLIO_WEB_SEARCH_PORT` | `8088` | Host port published by Compose |
| `CLIO_WEB_SEARCH_CONTACT_EMAIL` | empty | Crossref identification and Unpaywall access |
| `CLIO_WEB_SEARCH_OPENALEX_API_KEY` | empty | Optional free OpenAlex API key |
| `CLIO_WEB_SEARCH_DATA_VOLUME` | `clio-web-search-data` | Persistent Docker volume name |
| `CLIO_WEB_SEARCH_MAX_INPUT_BYTES` | `52428800` | Maximum uploaded document size |
| `CLIO_WEB_SEARCH_WORKERS` | `1` | Document conversion workers, from 1 to 16 |
| `CLIO_WEB_SEARCH_MAX_PENDING_JOBS` | `32` | Maximum queued and running jobs |
| `CLIO_WEB_SEARCH_CACHE_TTL_DAYS` | `7` | Completed-result retention, from 1 to 365 days |
| `CLIO_WEB_SEARCH_CACHE_MAX_BYTES` | `10737418240` | Persistent cache size budget |
| `CLIO_WEB_SEARCH_CONVERSION_TIMEOUT_S` | `900` | Per-document conversion timeout |
| `CLIO_WEB_SEARCH_REQUEST_TIMEOUT_S` | `30` | Search and metadata request timeout |

The container-internal service port remains `8080`. SearXNG and GROBID listen only inside the
container.

## LAN access

Set `CLIO_WEB_SEARCH_BIND_ADDRESS` to the server's LAN address when trusted remote clients need access:

```dotenv
CLIO_WEB_SEARCH_BIND_ADDRESS=10.0.0.102
CLIO_WEB_SEARCH_PORT=8089
```

Review the [security model](security_model.md) before exposing the service beyond loopback.

## Verify

```bash
curl --fail http://127.0.0.1:8088/healthz
curl --fail http://127.0.0.1:8088/readyz
curl --get http://127.0.0.1:8088/search \
  --data-urlencode 'q=physics-informed machine learning' \
  --data 'format=json&categories=science'
```

`/readyz` succeeds when the gateway, queue, SearXNG, and GROBID are ready.

## Upgrade

With Compose:

```bash
docker compose pull
docker compose up -d
```

The named volume preserves the document queue, cached objects, results, and SearXNG secret across
container replacement.
