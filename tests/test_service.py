"""Real API and durable-conversion tests."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from clio_search.config import Settings
from clio_search.documents import _public_failure
from clio_search.main import create_app


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "searxng_url": "http://127.0.0.1:9",
        "grobid_url": "http://127.0.0.1:9",
        "conversion_timeout_s": 60.0,
    }
    values.update(overrides)
    return Settings(**values)


def test_health_and_capabilities_degrade_without_contact_email(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        capabilities = client.get("/v1/capabilities").json()

    assert capabilities["search"]["provider"] == "searxng"
    assert capabilities["scholarly"]["unpaywall"] == {
        "enabled": False,
        "disabled_reason": "contact_email_missing",
    }


def test_readiness_requires_search_and_grobid(tmp_path: Path) -> None:
    response = AsyncMock()
    response.status_code = 200
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)):
        with TestClient(create_app(_settings(tmp_path))) as client:
            ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json()["checks"] == {
        "queue": "ready",
        "searxng": "ready",
        "grobid": "ready",
    }


def test_document_limit_returns_typed_error(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, max_input_bytes=4))) as client:
        response = client.post(
            "/v1/documents",
            files={"file": ("large.md", b"12345", "text/markdown")},
        )

    assert response.status_code == 413
    assert response.json()["code"] == "document_too_large"


def test_markdown_conversion_is_durable_and_content_deduplicated(tmp_path: Path) -> None:
    markdown = b"# Durable document\n\nThis content is converted once.\n"
    with TestClient(create_app(_settings(tmp_path))) as client:
        submitted = client.post(
            "/v1/documents",
            files={"file": ("evidence.md", markdown, "text/markdown")},
        )
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]

        deadline = time.monotonic() + 30
        result: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = client.get(f"/v1/documents/{job_id}")
            result = response.json()
            if result["status"] in {"complete", "failed"}:
                break
            time.sleep(0.1)

        assert result["status"] == "complete", result
        assert "Durable document" in result["result"]["markdown"]
        assert result["result"]["document"]["extractors"] == [
            {"name": "docling", "version": "2.119.0"}
        ]

        duplicate = client.post(
            "/v1/documents",
            files={"file": ("renamed.md", markdown, "text/markdown")},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == job_id
        assert duplicate.json()["status"] == "complete"


def test_unknown_conversion_has_typed_error(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/v1/documents/not-present")

    assert response.status_code == 404
    assert response.json()["code"] == "conversion_not_found"


def test_failed_content_job_is_requeued_on_resubmission(tmp_path: Path) -> None:
    markdown = b"# Retry me\n"
    with TestClient(create_app(_settings(tmp_path))) as client:
        submitted = client.post(
            "/v1/documents",
            files={"file": ("retry.md", markdown, "text/markdown")},
        ).json()
        job_id = submitted["id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get(f"/v1/documents/{job_id}").json()
            if status["status"] == "complete":
                break
            time.sleep(0.1)
        assert status["status"] == "complete"

        import sqlite3

        with sqlite3.connect(tmp_path / "jobs.sqlite3") as database:
            database.execute(
                "UPDATE jobs SET status = 'failed', error = 'transient' WHERE id = ?",
                (job_id,),
            )
            database.commit()

        retried = client.post(
            "/v1/documents",
            files={"file": ("retry.md", markdown, "text/markdown")},
        )
        assert retried.status_code == 202
        assert retried.json() == {"id": job_id, "status": "queued", "retry_after_s": 2}


def test_public_conversion_failure_is_typed_and_bounded() -> None:
    failure = _public_failure(RuntimeError("compiler failed\n" * 200))

    assert failure["code"] == "document_conversion_failed"
    assert failure["retryable"] is True
    assert len(failure["message"]) <= 800
    assert "\n" not in failure["message"]


def test_expired_completed_job_is_pruned_on_restart(tmp_path: Path) -> None:
    """Expired job state and owned files are removed when the service starts."""

    markdown = b"# Expire me\n"
    settings = _settings(tmp_path, cache_ttl_days=1)
    with TestClient(create_app(settings)) as client:
        submitted = client.post(
            "/v1/documents",
            files={"file": ("old.md", markdown, "text/markdown")},
        ).json()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get(f"/v1/documents/{submitted['id']}").json()
            if status["status"] == "complete":
                break
            time.sleep(0.1)
        assert status["status"] == "complete"

    import sqlite3

    with sqlite3.connect(tmp_path / "jobs.sqlite3") as database:
        row = database.execute(
            "SELECT input_path, result_path FROM jobs WHERE id = ?", (submitted["id"],)
        ).fetchone()
        assert row is not None
        database.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            (time.time() - 172_800, submitted["id"]),
        )
        database.commit()

    with TestClient(create_app(settings)) as client:
        assert client.get(f"/v1/documents/{submitted['id']}").status_code == 404
    assert not Path(row[0]).exists()
    assert not Path(row[1]).exists()
