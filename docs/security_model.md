# Security model

CLIO Web Search is a self-hosted service for trusted clients. It does not provide authentication,
authorization, TLS termination, or an Internet-facing rate limiter.

## Trust boundary

- Bind to `127.0.0.1` when the MCP runs on the same host.
- Bind to an explicit private address only for trusted LAN clients.
- Put authentication, TLS, and request limits in a reverse proxy before any broader exposure.
- Do not publish the container port directly to the public Internet.

## Container behavior

The container starts as root only to repair ownership of `/var/lib/clio-web-search`. The entrypoint
then drops permanently to UID/GID `65534` before starting SearXNG, GROBID, Valkey, and the gateway.
The Compose configuration enables `no-new-privileges` and disables core dumps.

SearXNG and GROBID bind to loopback inside the container. The HTTP gateway and Valkey RESP ports
are published on the explicitly configured host interface. Keep the trusted default off public
interfaces.

## Data and secrets

Submitted documents, converted results, queue state, and progress logs are stored in
`/var/lib/clio-web-search`; Valkey's Docket state is stored in its `/data`-backed subvolume. Protect
both according to the sensitivity of the documents and task context being processed.

Trusted mode deliberately has no Valkey password and must be bound only to a trusted LAN/Tailscale
address. Setting `CLIO_WEB_SEARCH_TASK_BACKEND_API_TOKEN` enables authenticated bootstrap: the API
derives stable credentials, provisions a Valkey ACL user limited to that agent's queue prefix, and
returns the credential only to an authorized caller. TLS mode builds Valkey with TLS support and
requires explicitly mounted certificate material.

The optional contact email is sent to scholarly providers that support polite identification. The
optional OpenAlex key is sent only to OpenAlex. Environment variables can be visible through Docker
administration interfaces, so restrict access to the Docker host.

## External content

Search engines and scholarly APIs are external services. Documents are untrusted inputs processed
by Docling and GROBID. Keep the image current, limit accepted document size, bound the queue, and
run the service on an isolated host or network appropriate for the data being handled.

See [Deployment](deployment.md) for network and resource configuration.
