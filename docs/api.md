# HTTP API

CLIO Web Search exposes a small HTTP surface for health, search, DOI resolution, and durable
document conversion. Interactive OpenAPI documentation is available at `/docs` on a running
installation.

## Health

### `GET /healthz`

Returns gateway liveness and the service version.

### `GET /readyz`

Returns `200` only after Docling's worker pipeline has warmed and SearXNG and GROBID are ready;
otherwise returns `503` with per-service status. During application startup the endpoint does not
accept traffic until warmup succeeds.

## Capabilities

### `GET /v1/capabilities`

Reports the service version, search provider, supported document formats, extractor versions,
queue counts, scholarly-provider configuration, and public Valkey discovery metadata.

### `POST /v1/task-backend/session`

Accepts `{"agent_id":"..."}` and returns the stable Docket queue name and Valkey connection
metadata for that local Web MCP installation. Trusted mode requires no credentials. Authenticated
mode requires the configured bearer token and returns a scoped Valkey ACL username and password.

## Search

### `GET|POST /search`

Proxies the SearXNG search API. A JSON search typically supplies `q`, `format=json`, and an optional
category:

```bash
curl --get http://127.0.0.1:8089/search \
  --data-urlencode 'q=physics-informed machine learning' \
  --data 'format=json&categories=science'
```

## DOI resolution

### `POST /v1/doi/resolve`

```bash
curl http://127.0.0.1:8089/v1/doi/resolve \
  --header 'Content-Type: application/json' \
  --data '{"doi":"10.1038/s42254-021-00314-5"}'
```

The response contains normalized metadata, lawful access candidates, queried sources, and typed
warnings.

## Documents

### `POST /v1/documents`

Submit multipart form data with a required `file` and optional `source_url` and `doi` fields:

```bash
curl http://127.0.0.1:8089/v1/documents \
  --form 'file=@paper.pdf' \
  --form 'doi=10.1038/s42254-021-00314-5'
```

A new job returns `202` with `id`, `status`, and `retry_after_s`. Content already converted by the
same pipeline returns the existing result.

### `GET /v1/documents/{job_id}`

Returns `202` while queued or running and `200` when complete or failed. The payload includes the
current progress percentage, status message, and a bounded ordered `events` log. Completed results
contain Markdown and a normalized document object. Scholarly PDFs can include metadata,
references, citation contexts, and the contributing extractors.

Conversion itself has no overall timeout. The job continues until completion, failure, service
shutdown, or explicit cancellation.

### `GET /v1/documents/{job_id}/events`

Returns an ordered event page. Use `after_sequence` as a cursor and `limit` from 1 to 500. Each
event includes `sequence`, `created_at`, `level`, `progress`, `stage`, and a sanitized message.

### `POST /v1/documents/{job_id}/cancel`

Cancels queued or running work and returns its durable `cancelled` state. Cancelling active Docling
work terminates that worker process; the service warms a replacement before the worker becomes
available for another job.

## Errors

Service errors use a stable envelope:

```json
{
  "code": "document_too_large",
  "stage": "input_validation",
  "message": "The document exceeds the 52428800-byte conversion limit.",
  "retryable": false,
  "remediation": "Provide a smaller document or raise the configured input limit.",
  "conversion_id": null,
  "details": {}
}
```
