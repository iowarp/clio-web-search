# syntax=docker/dockerfile:1.7
ARG GROBID_IMAGE=grobid/grobid:0.9.0-crf@sha256:24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849
ARG VALKEY_VERSION=8.1.9
ARG VALKEY_SHA256=f7e927534aaeb3a5f4410375c3e6f2f0c1b9257db8bc573e68f9e2dbb11344f4
FROM ghcr.io/astral-sh/uv:0.8.12 AS uv
FROM ${GROBID_IMAGE}

ARG VALKEY_VERSION
ARG VALKEY_SHA256

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl libgl1 libglib2.0-0 libssl-dev libxcb1 \
        pkg-config python3.12 python3.12-dev python3.12-venv \
    && curl --fail --silent --show-error --location \
        --output /tmp/valkey.tar.gz \
        "https://github.com/valkey-io/valkey/archive/refs/tags/${VALKEY_VERSION}.tar.gz" \
    && echo "${VALKEY_SHA256}  /tmp/valkey.tar.gz" | sha256sum --check --strict \
    && mkdir /tmp/valkey \
    && tar xzf /tmp/valkey.tar.gz --strip-components=1 -C /tmp/valkey \
    && make -C /tmp/valkey -j"$(nproc)" BUILD_TLS=yes \
    && make -C /tmp/valkey PREFIX=/usr/local install \
    && valkey-server --version \
    && rm -rf /tmp/valkey /tmp/valkey.tar.gz /var/lib/apt/lists/*

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
COPY container/valkey.conf /etc/clio-web-search/valkey.conf
RUN chmod 0755 /usr/local/bin/clio-web-search-entrypoint \
    && mkdir -p /var/lib/clio-web-search/valkey /opt/grobid/grobid-home/tmp \
    && chown -R 65534:65534 /var/lib/clio-web-search /opt/grobid/grobid-home/tmp \
    && chmod 0770 /var/lib/clio-web-search /var/lib/clio-web-search/valkey \
        /opt/grobid/grobid-home/tmp

ENV CLIO_WEB_SEARCH_SEARXNG_URL=http://127.0.0.1:8888 \
    CLIO_WEB_SEARCH_GROBID_URL=http://127.0.0.1:8070 \
    CLIO_WEB_SEARCH_DATA_DIR=/var/lib/clio-web-search \
    CLIO_WEB_SEARCH_TASK_BACKEND_PUBLIC_PORT=8090 \
    HF_HOME=/var/lib/clio-web-search/huggingface \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models \
    JAVA_OPTS="-Xms256m -Xmx1536m -XX:MaxDirectMemorySize=256m -XX:+ExitOnOutOfMemoryError" \
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    NUMEXPR_NUM_THREADS=2

LABEL org.opencontainers.image.title="CLIO Web Search" \
      org.opencontainers.image.description="Self-hosted web search and document understanding for AI agents" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.version="0.3.0" \
      org.opencontainers.image.source="https://github.com/iowarp/clio-web-search"

VOLUME ["/var/lib/clio-web-search"]
EXPOSE 8080 6379
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8080/readyz >/dev/null || exit 1

ENTRYPOINT ["/tini", "-s", "--", "/usr/local/bin/clio-web-search-entrypoint"]
