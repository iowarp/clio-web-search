#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    chown -R 65534:65534 /var/lib/clio-search
    exec setpriv --reuid=65534 --regid=65534 --clear-groups "$0" "$@"
fi

mkdir -p /var/lib/clio-search /tmp/clio-search
settings_path="$(/app/.venv/bin/python -m clio_search.configure)"

terminate() {
    kill -TERM "${gateway_pid:-}" "${searxng_pid:-}" "${grobid_pid:-}" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap terminate INT TERM EXIT

cd /opt/grobid
./grobid-service/bin/grobid-service > /tmp/clio-search/grobid.log 2>&1 &
grobid_pid=$!

cd /opt/searxng
SEARXNG_SETTINGS_PATH="$settings_path" \
    ./.venv/bin/granian searx.webapp:app \
    --interface wsgi --host 127.0.0.1 --port 8888 \
    > /tmp/clio-search/searxng.log 2>&1 &
searxng_pid=$!

cd /app
./.venv/bin/uvicorn clio_search.main:app --host 0.0.0.0 --port 8080 \
    > /tmp/clio-search/gateway.log 2>&1 &
gateway_pid=$!

while kill -0 "$gateway_pid" "$searxng_pid" "$grobid_pid" 2>/dev/null; do
    sleep 2
done

echo "A CLIO Search child process exited" >&2
tail -100 /tmp/clio-search/*.log >&2 || true
exit 1
