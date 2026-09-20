"""Static acceptance checks for the unified container startup contract."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_docling_warmup_precedes_grobid_startup() -> None:
    """The entrypoint avoids loading Docling and GROBID models concurrently."""

    entrypoint = (_ROOT / "container" / "entrypoint.sh").read_text(encoding="utf-8")

    gateway_start = entrypoint.index("uvicorn clio_web_search.main:app")
    warmup_probe = entrypoint.index("http://127.0.0.1:8080/healthz")
    grobid_start = entrypoint.index("./grobid-service/bin/grobid-service")

    assert gateway_start < warmup_probe < grobid_start


def test_container_has_host_resource_guards() -> None:
    """The image and Compose defaults constrain heap, threads, and total memory."""

    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "-Xmx1536m" in dockerfile
    assert "OMP_NUM_THREADS=2" in dockerfile
    assert 'mem_limit: "${CLIO_WEB_SEARCH_MEMORY_LIMIT:-5g}"' in compose


def test_operator_stop_exits_cleanly_instead_of_reporting_child_failure() -> None:
    """SIGTERM from Docker is a successful lifecycle stop."""

    entrypoint = (_ROOT / "container" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "trap shutdown INT TERM" in entrypoint
    assert "trap cleanup EXIT" in entrypoint
    shutdown = entrypoint[entrypoint.index("shutdown() {") : entrypoint.index("trap shutdown")]
    assert "trap - INT TERM EXIT" in shutdown
    assert "cleanup" in shutdown
    assert "exit 0" in shutdown
