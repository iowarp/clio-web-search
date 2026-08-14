# syntax=docker/dockerfile:1.7
ARG GROBID_IMAGE=grobid/grobid:0.9.0-crf@sha256:24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849
FROM ghcr.io/astral-sh/uv:0.8.12 AS uv
FROM ${GROBID_IMAGE}

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl g++ libgl1 libglib2.0-0 libxcb1 \
        python3.12 python3.12-dev python3.12-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

ARG SEARXNG_COMMIT=e8e710e42a3ab2bce27d1f97e51d8d4ccaa80871
RUN mkdir -p /opt/searxng \
    && uv venv --python python3.12 /opt/searxng/.venv \
    && mkdir -p /opt/searxng-source \
    && curl --fail --silent --show-error --location \
        "https://github.com/searxng/searxng/archive/${SEARXNG_COMMIT}.tar.gz" \
        | tar xz --strip-components=1 -C /opt/searxng-source \
    && printf '%s\n' \
        '# SPDX-License-Identifier: AGPL-3.0-or-later' \
        'VERSION_STRING = "2026.8.11+e8e710e42"' \
        'VERSION_TAG = "2026.8.11+e8e710e42"' \
        'DOCKER_TAG = "2026.8.11-e8e710e42"' \
        'GIT_URL = "https://github.com/searxng/searxng"' \
        'GIT_BRANCH = "master"' \
        > /opt/searxng-source/searx/version_frozen.py \
    && uv pip install --python /opt/searxng/.venv/bin/python \
        --requirements /opt/searxng-source/requirements.txt \
        --requirements /opt/searxng-source/requirements-server.txt \
        setuptools \
    && uv pip install --python /opt/searxng/.venv/bin/python \
        --no-build-isolation /opt/searxng-source \
    && rm -rf /opt/searxng-source

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
RUN /app/.venv/bin/docling-tools models download \
        layout tableformerv2 rapidocr \
        --output-dir /opt/docling-models
COPY src ./src
RUN uv sync --frozen --no-dev

COPY container/datacite.py container/patch_searxng.py /opt/clio-web-search-build/
RUN /opt/searxng/.venv/bin/python /opt/clio-web-search-build/patch_searxng.py \
    && rm -rf /opt/clio-web-search-build

COPY container/entrypoint.sh /usr/local/bin/clio-web-search-entrypoint
RUN chmod 0755 /usr/local/bin/clio-web-search-entrypoint \
    && mkdir -p /var/lib/clio-web-search /opt/grobid/grobid-home/tmp \
    && chown 65534:65534 /opt/grobid/grobid-home/tmp \
    && chmod 0770 /var/lib/clio-web-search /opt/grobid/grobid-home/tmp

ENV CLIO_WEB_SEARCH_SEARXNG_URL=http://127.0.0.1:8888 \
    CLIO_WEB_SEARCH_GROBID_URL=http://127.0.0.1:8070 \
    CLIO_WEB_SEARCH_DATA_DIR=/var/lib/clio-web-search \
    HF_HOME=/var/lib/clio-web-search/huggingface \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models

LABEL org.opencontainers.image.title="CLIO Web Search" \
      org.opencontainers.image.description="Self-hosted web search and document understanding for AI agents" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.version="0.2.0" \
      org.opencontainers.image.source="https://github.com/iowarp/clio-web-search"

VOLUME ["/var/lib/clio-web-search"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8080/healthz >/dev/null || exit 1

ENTRYPOINT ["/tini", "-s", "--", "/usr/local/bin/clio-web-search-entrypoint"]
