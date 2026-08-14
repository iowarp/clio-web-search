# Security model

CLIO Web Search is a self-hosted service for trusted clients. It does not provide authentication,
authorization, TLS termination, or an Internet-facing rate limiter.

## Trust boundary

- Bind to `127.0.0.1` when the MCP runs on the same host.
- Bind to an explicit private address only for trusted LAN clients.
- Put authentication, TLS, and request limits in a reverse proxy before any broader exposure.
- Do not publish the container port directly to the public Internet.

## Container behavior

The container starts as root only to repair ownership of `/var/lib/clio-web-search`. The entrypoint then
drops permanently to UID/GID `65534` before starting SearXNG, GROBID, and the gateway. The Compose
configuration enables `no-new-privileges` and disables core dumps.

SearXNG and GROBID bind to loopback inside the container. Only the gateway on port `8080` is
published.

## Data and secrets

Submitted documents, converted results, queue state, cache data, and the generated SearXNG secret
are stored in `/var/lib/clio-web-search`. Protect and back up the Docker volume according to the
sensitivity of the documents being processed.

The optional contact email is sent to scholarly providers that support polite identification. The
optional OpenAlex key is sent only to OpenAlex. Environment variables can be visible through Docker
administration interfaces, so restrict access to the Docker host.

## External content

Search engines and scholarly APIs are external services. Documents are untrusted inputs processed
by Docling and GROBID. Keep the image current, limit accepted document size, bound the queue, and
run the service on an isolated host or network appropriate for the data being handled.

See [Deployment](deployment.md) for network and resource configuration.
