# HTTP API

CLIO Web Search exposes a small HTTP surface for health, search, DOI resolution, and durable
document conversion. Interactive OpenAPI documentation is available at `/docs` on a running
installation.

## Health

### `GET /healthz`

Returns gateway liveness and the service version.

### `GET /readyz`

Returns `200` when the queue, SearXNG, and GROBID are ready; otherwise returns `503` with per-service
status.

## Capabilities

### `GET /v1/capabilities`

Reports the service version, search provider, supported document formats, extractor versions,
queue counts, and active scholarly-provider configuration.

## Search

### `GET|POST /search`

Proxies the SearXNG search API. A JSON search typically supplies `q`, `format=json`, and an optional
category:

```bash
curl --get http://127.0.0.1:8088/search \
  --data-urlencode 'q=physics-informed machine learning' \
  --data 'format=json&categories=science'
```

## DOI resolution

### `POST /v1/doi/resolve`

```bash
curl http://127.0.0.1:8088/v1/doi/resolve \
  --header 'Content-Type: application/json' \
  --data '{"doi":"10.1038/s42254-021-00314-5"}'
```

The response contains normalized metadata, lawful access candidates, queried sources, and typed
warnings.

## Documents

### `POST /v1/documents`

Submit multipart form data with a required `file` and optional `source_url` and `doi` fields:

```bash
curl http://127.0.0.1:8088/v1/documents \
  --form 'file=@paper.pdf' \
  --form 'doi=10.1038/s42254-021-00314-5'
```

A new job returns `202` with `id`, `status`, and `retry_after_s`. Content already converted by the
same pipeline returns the existing result.

### `GET /v1/documents/{job_id}`

Returns `202` while queued or running and `200` when complete or failed. Completed results contain
Markdown and a normalized document object. Scholarly PDFs can include metadata, references,
citation contexts, and the contributing extractors.

## Errors

Service errors use a stable envelope:

```json
{
  "code": "document_too_large",
  "message": "document exceeds 52428800 bytes",
  "retryable": false,
  "details": {}
}
```
