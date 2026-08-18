# Other container platforms

CLIO Web Search publishes one OCI image. Container runtimes use the same application and image;
runtime-specific behavior is documented here.

## Support status

| Runtime | Status |
| --- | --- |
| Docker | Supported |
| Rootless Podman | Compatible; persistent storage must be verified on the target filesystem |
| Apptainer | Planned HPC target; not yet verified |
| SingularityCE | Best effort after Apptainer support |
| Kubernetes | OCI-compatible; no maintained manifests or Helm chart |
| udocker | Not supported |

## Podman

Podman can use the published image directly:

```bash
podman run --detach \
  --name clio-web-search \
  --publish 127.0.0.1:8089:8080 \
  --publish 127.0.0.1:8090:6379 \
  --env CLIO_WEB_SEARCH_CONTACT_EMAIL=you@example.org \
  --volume clio-web-search-data:/var/lib/clio-web-search \
  ghcr.io/iowarp/clio-web-search:0.2.0
```

Docker-compatible Podman shims can use the equivalent `docker run` command. `podman compose`
requires a Compose provider and is not assumed to be present.

Rootless Podman stores image layers in the user's container storage. Check quota and filesystem
support before moving that storage to a shared or network filesystem. Named volumes are the safest
starting point for document data; verify bind-mount ownership on the target system.

## Apptainer

Apptainer is a target for scheduler-managed HPC environments, but the current image is not yet
declared compatible. Validation must cover:

- execution as the caller's arbitrary UID
- writable data and temporary directories with a read-only image
- GROBID, SearXNG, and gateway startup
- health, search, and document conversion on a compute node
- persistent storage on the site's scratch filesystem

Apptainer uses the host network rather than Docker-style port publishing. Site scheduler and login
node policies still apply.

## Portability requirements

First-class support for additional runtimes requires the image to run without startup `chown`,
accept an arbitrary non-root UID, confine writes to configured data and temporary directories, and
pass live runtime-specific smoke tests.

See [Deployment](deployment.md) for Docker and Compose configuration.
