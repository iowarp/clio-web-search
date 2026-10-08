"""Search-only (slim) deployments without the optional ``documents`` extra."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from clio_web_search.config import Settings
from clio_web_search.main import DOCUMENTS_NOT_INSTALLED, create_app


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "searxng_url": "http://127.0.0.1:9",
        "grobid_url": "http://127.0.0.1:9",
    }
    values.update(overrides)
    return Settings(**values)


def _assert_not_installed(response: Any) -> None:
    assert response.status_code == 501
    body = response.json()
    assert body["code"] == DOCUMENTS_NOT_INSTALLED
    assert body["message"] == "Document conversion is not installed in this deployment."
    assert body["retryable"] is False
    assert body["remediation"]


def test_disabled_documents_are_reported_absent_in_capabilities(tmp_path: Path) -> None:
    tested_app = create_app(_settings(tmp_path, documents_enabled=False))
    with TestClient(tested_app) as client:
        capabilities = client.get("/v1/capabilities").json()

    assert tested_app.state.queue is None
    assert capabilities["search"] == {"provider": "searxng", "path": "/search"}
    assert capabilities["documents"]["available"] is False
    assert capabilities["documents"]["disabled_reason"] == DOCUMENTS_NOT_INSTALLED
    assert capabilities["documents"]["formats"] == []
    assert capabilities["documents"]["extractors"] == []
    assert capabilities["documents"]["queue"] is None
    assert capabilities["scholarly"]["doi_resolution"] is True


def test_document_endpoints_return_typed_not_installed_error(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, documents_enabled=False))) as client:
        _assert_not_installed(
            client.post("/v1/documents", files={"file": ("a.md", b"# a", "text/markdown")})
        )
        _assert_not_installed(client.get("/v1/documents/abc"))
        _assert_not_installed(client.get("/v1/documents/abc/events"))
        _assert_not_installed(client.post("/v1/documents/abc/cancel"))


def test_search_only_readiness_probes_searxng_but_not_grobid(tmp_path: Path) -> None:
    response = AsyncMock()
    response.status_code = 200
    probe = AsyncMock(return_value=response)
    with patch("httpx.AsyncClient.get", new=probe):
        with TestClient(create_app(_settings(tmp_path, documents_enabled=False))) as client:
            ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json()["checks"] == {"searxng": "ready"}
    probed = [str(call.args[0]) for call in probe.await_args_list]
    assert probed == ["http://127.0.0.1:9/config"]


def test_search_is_still_proxied_without_documents(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, documents_enabled=False))) as client:
        response = client.get("/search", params={"q": "valkey", "format": "json"})

    # The unreachable test SearXNG yields the typed search error, not a document error.
    assert response.status_code == 502
    assert response.json()["code"] == "searxng_unavailable"


def test_auto_detection_disables_documents_when_docling_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clio_web_search.main.documents_installed", lambda: False)
    tested_app = create_app(_settings(tmp_path))
    with TestClient(tested_app) as client:
        capabilities = client.get("/v1/capabilities").json()

    assert tested_app.state.queue is None
    assert capabilities["documents"]["available"] is False


def test_explicitly_enabled_documents_without_docling_fail_clearly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clio_web_search.main.documents_installed", lambda: False)
    with pytest.raises(RuntimeError, match="documents' extra"):
        create_app(_settings(tmp_path, documents_enabled=True))


def test_gateway_imports_without_docling(tmp_path: Path) -> None:
    """The slim image has no Docling; importing the gateway must not need it."""

    script = (
        "import sys\n"
        "sys.modules['docling'] = None\n"
        "import clio_web_search.main as main\n"
        "assert not main.documents_installed()\n"
        "assert main.app.state.queue is None\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"CLIO_WEB_SEARCH_DATA_DIR": str(tmp_path), "PATH": ""},
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
