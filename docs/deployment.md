# Deployment

CLIO Web Search is distributed as a Linux container for `amd64` and `arm64` hosts. The image
contains SearXNG, Docling, GROBID, Valkey, their models, and the HTTP gateway.

## Docker

`docker run` pulls the published image directly from GHCR. It does not require Git.

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

Omit `CLIO_WEB_SEARCH_CONTACT_EMAIL` if Unpaywall enrichment is not needed. Use a release digest in
place of `:0.3.0` for an immutable deployment.

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
| `CLIO_WEB_SEARCH_IMAGE` | `ghcr.io/iowarp/clio-web-search:0.3.0` | Compose image tag or digest |
| `CLIO_WEB_SEARCH_BIND_ADDRESS` | `127.0.0.1` | Host address published by Compose |
| `CLIO_WEB_SEARCH_PORT` | `8089` | HTTP host port published by Compose |
| `CLIO_WEB_SEARCH_CONTACT_EMAIL` | empty | Crossref identification and Unpaywall access |
| `CLIO_WEB_SEARCH_OPENALEX_API_KEY` | empty | Optional free OpenAlex API key |
| `CLIO_WEB_SEARCH_DATA_VOLUME` | `clio-web-search-data` | Persistent Docker volume name |
| `CLIO_WEB_SEARCH_MAX_INPUT_BYTES` | `52428800` | Maximum uploaded document size |
| `CLIO_WEB_SEARCH_WORKERS` | `1` | Document conversion workers, from 1 to 16 |
| `CLIO_WEB_SEARCH_MAX_PENDING_JOBS` | `32` | Maximum queued and running jobs |
| `CLIO_WEB_SEARCH_CACHE_TTL_DAYS` | `7` | Completed-result retention, from 1 to 365 days |
| `CLIO_WEB_SEARCH_CACHE_MAX_BYTES` | `10737418240` | Persistent cache size budget |
| `CLIO_WEB_SEARCH_REQUEST_TIMEOUT_S` | `30` | Search and metadata request timeout |
| `CLIO_WEB_SEARCH_GROBID_REQUEST_TIMEOUT_S` | `900` | Per-request GROBID network timeout |
| `CLIO_WEB_SEARCH_PROGRESS_INTERVAL_S` | `5` | Active-conversion heartbeat interval |
| `CLIO_WEB_SEARCH_DEPLOYMENT_ID` | `homelab` | Stable prefix for per-agent Docket queues |
| `CLIO_WEB_SEARCH_VALKEY_PORT` | `8090` | Trusted-interface RESP port advertised to local MCPs |
| `CLIO_WEB_SEARCH_VALKEY_DATA_PATH` | `/data/clio-web-search/valkey` | Valkey AOF storage on the large data drive |
| `CLIO_WEB_SEARCH_MEMORY_LIMIT` | `5g` | Hard container memory ceiling protecting the host from startup or conversion pressure |
| `CLIO_WEB_SEARCH_JAVA_OPTS` | `-Xms256m -Xmx1536m -XX:MaxDirectMemorySize=256m -XX:+ExitOnOutOfMemoryError` | GROBID JVM memory policy; increase only when the host has measured headroom |
| `CLIO_WEB_SEARCH_NATIVE_THREADS` | `2` | Native worker threads used by Docling numerical libraries |
| `CLIO_WEB_SEARCH_TASK_BACKEND_API_TOKEN` | empty | Enables authenticated discovery and scoped ACL users |
| `CLIO_WEB_SEARCH_TASK_BACKEND_TLS` | `false` | Enables `rediss://`; certificate, key, and CA paths are then required |

There is deliberately no document-conversion timeout. A conversion runs until it completes, fails,
the service shuts down, or a client cancels it. The GROBID setting above bounds an individual
network request, not the overall job.

The container-internal HTTP port remains `8080`. Valkey uses container port `6379`. SearXNG and
GROBID listen only inside the container. Publish Valkey only on a trusted LAN or Tailscale address;
it uses RESP and cannot be exposed as an HTTP `/valkey` path.

## LAN access

Set `CLIO_WEB_SEARCH_BIND_ADDRESS` to the server's LAN address when trusted remote clients need access:

```dotenv
CLIO_WEB_SEARCH_BIND_ADDRESS=10.0.0.102
CLIO_WEB_SEARCH_PORT=8089
```

Review the [security model](security_model.md) before exposing the service beyond loopback.

## Verify

```bash
curl --fail http://127.0.0.1:8089/healthz
curl --fail http://127.0.0.1:8089/readyz
curl --get http://127.0.0.1:8089/search \
  --data-urlencode 'q=physics-informed machine learning' \
  --data 'format=json&categories=science'
```

`/readyz` succeeds only after the gateway has initialized and warmed every Docling worker and
SearXNG and GROBID are ready. The Docker health check uses this readiness endpoint, so the first PDF
never pays the pipeline-initialization cost.

## Upgrade

With Compose:

```bash
docker compose pull
docker compose up -d
```

The named volume preserves the document queue, progress logs, cached objects, results, and SearXNG
secret. The `/data` bind mount preserves Valkey's append-only task state. No migration from pre-0.2
paths is performed; supported installations use `/var/lib/clio-web-search`.
