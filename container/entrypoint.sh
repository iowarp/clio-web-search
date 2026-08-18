#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    chown -R 65534:65534 /var/lib/clio-web-search
    exec setpriv --reuid=65534 --regid=65534 --clear-groups "$0" "$@"
fi

mkdir -p /var/lib/clio-web-search /tmp/clio-web-search
settings_path="$(/app/.venv/bin/python -m clio_web_search.configure)"

task_auth_token="${CLIO_WEB_SEARCH_TASK_BACKEND_API_TOKEN:-}"
valkey_password=""
set -- /etc/clio-web-search/valkey.conf
if [ -n "$task_auth_token" ]; then
    password_path=/var/lib/clio-web-search/valkey-admin.key
    credential_path=/var/lib/clio-web-search/task-credential.key
    if [ ! -s "$password_path" ]; then
        umask 077
        /app/.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))' > "$password_path"
    fi
    if [ ! -s "$credential_path" ]; then
        umask 077
        /app/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))' \
            > "$credential_path"
    fi
    valkey_password="$(tr -d '\r\n' < "$password_path")"
    CLIO_WEB_SEARCH_TASK_BACKEND_CREDENTIAL_SECRET="$(tr -d '\r\n' < "$credential_path")"
    export CLIO_WEB_SEARCH_TASK_BACKEND_CREDENTIAL_SECRET
    set -- "$@" --requirepass "$valkey_password"
fi

task_scheme=redis
if [ "${CLIO_WEB_SEARCH_TASK_BACKEND_TLS:-false}" = "true" ]; then
    : "${CLIO_WEB_SEARCH_TASK_BACKEND_TLS_CERT_FILE:?TLS cert file is required}"
    : "${CLIO_WEB_SEARCH_TASK_BACKEND_TLS_KEY_FILE:?TLS key file is required}"
    : "${CLIO_WEB_SEARCH_TASK_BACKEND_CA_PATH:?TLS CA file is required}"
    task_scheme=rediss
    set -- "$@" --port 0 --tls-port 6379
    set -- "$@" --tls-cert-file "$CLIO_WEB_SEARCH_TASK_BACKEND_TLS_CERT_FILE"
    set -- "$@" --tls-key-file "$CLIO_WEB_SEARCH_TASK_BACKEND_TLS_KEY_FILE"
    set -- "$@" --tls-ca-cert-file "$CLIO_WEB_SEARCH_TASK_BACKEND_CA_PATH"
    set -- "$@" --tls-auth-clients no
fi

if [ -n "$valkey_password" ]; then
    CLIO_WEB_SEARCH_TASK_BACKEND_URL="${task_scheme}://default:${valkey_password}@127.0.0.1:6379/0"
else
    CLIO_WEB_SEARCH_TASK_BACKEND_URL="${task_scheme}://127.0.0.1:6379/0"
fi
if [ "$task_scheme" = "rediss" ]; then
    CLIO_WEB_SEARCH_TASK_BACKEND_URL="${CLIO_WEB_SEARCH_TASK_BACKEND_URL}?ssl_ca_certs=${CLIO_WEB_SEARCH_TASK_BACKEND_CA_PATH}"
fi
export CLIO_WEB_SEARCH_TASK_BACKEND_URL

terminate() {
    kill -TERM "${gateway_pid:-}" "${searxng_pid:-}" "${grobid_pid:-}" \
        "${valkey_pid:-}" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap terminate INT TERM EXIT

valkey-server "$@" \
    > /tmp/clio-web-search/valkey.log 2>&1 &
valkey_pid=$!

cd /app
./.venv/bin/uvicorn clio_web_search.main:app --host 0.0.0.0 --port 8080 \
    > /tmp/clio-web-search/gateway.log 2>&1 &
gateway_pid=$!

# Warm Docling before loading GROBID's multi-gigabyte model set.  Serializing
# those two initialization phases avoids a transient memory and disk-I/O spike
# on the small homelab host.  Uvicorn does not serve healthz until its lifespan
# startup (and therefore the real Docling warmup conversion) has completed.
until curl --fail --silent http://127.0.0.1:8080/healthz >/dev/null; do
    if ! kill -0 "$gateway_pid" "$valkey_pid" 2>/dev/null; then
        echo "CLIO Web Search failed during Docling startup warmup" >&2
        tail -100 /tmp/clio-web-search/*.log >&2 || true
        exit 1
    fi
    sleep 2
done
echo "Docling startup warmup complete" >&2

cd /opt/grobid
./grobid-service/bin/grobid-service > /tmp/clio-web-search/grobid.log 2>&1 &
grobid_pid=$!

cd /opt/searxng
SEARXNG_SETTINGS_PATH="$settings_path" \
    ./.venv/bin/granian searx.webapp:app \
    --interface wsgi --host 127.0.0.1 --port 8888 \
    > /tmp/clio-web-search/searxng.log 2>&1 &
searxng_pid=$!

while kill -0 "$gateway_pid" "$searxng_pid" "$grobid_pid" "$valkey_pid" 2>/dev/null; do
    sleep 2
done

echo "A CLIO Web Search child process exited" >&2
tail -100 /tmp/clio-web-search/*.log >&2 || true
exit 1
