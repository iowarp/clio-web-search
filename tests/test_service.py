"""Real API and durable-conversion tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from clio_web_search.config import Settings
from clio_web_search.docling_worker import ConversionCancelledError, ProgressCallback
from clio_web_search.documents import public_failure
from clio_web_search.main import create_app


class _TestWorker:
    """Fast deterministic conversion worker used at API seams."""

    starts = 0
    stops = 0

    def __init__(self) -> None:
        self._ready = False

    @property
    def ready(self) -> bool:
        """Return whether test warmup completed."""

        return self._ready

    async def start(self) -> None:
        """Record deterministic startup warmup."""

        type(self).starts += 1
        self._ready = True

    async def convert(
        self,
        path: Path,
        *,
        cancelled: asyncio.Event,
        on_progress: ProgressCallback,
        heartbeat_s: float,
    ) -> dict[str, Any]:
        """Return real input text through the worker protocol."""

        del heartbeat_s
        if cancelled.is_set():
            raise ConversionCancelledError
        await on_progress(25, "docling", "Test worker is converting", "info")
        markdown = path.read_text(encoding="utf-8")
        await on_progress(75, "export", "Test worker exported the document", "info")
        return {"markdown": markdown, "structure": {"text": markdown}}

    async def stop(self) -> None:
        """Record deterministic worker shutdown."""

        type(self).stops += 1
        self._ready = False


class _BlockingWorker(_TestWorker):
    """Worker that remains active until cancellation is requested."""

    async def convert(
        self,
        path: Path,
        *,
        cancelled: asyncio.Event,
        on_progress: ProgressCallback,
        heartbeat_s: float,
    ) -> dict[str, Any]:
        """Emit progress until the queue signals cancellation."""

        del path, heartbeat_s
        await on_progress(20, "docling", "Blocking conversion started", "info")
        await cancelled.wait()
        raise ConversionCancelledError


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "searxng_url": "http://127.0.0.1:9",
        "grobid_url": "http://127.0.0.1:9",
        "progress_interval_s": 0.01,
    }
    values.update(overrides)
    return Settings(**values)


def _test_app(settings: Settings) -> Any:
    """Create an app whose worker obeys the production process contract."""

    return create_app(settings, worker_factory=_TestWorker)


def test_health_and_capabilities_degrade_without_contact_email(tmp_path: Path) -> None:
    with TestClient(_test_app(_settings(tmp_path))) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        capabilities = client.get("/v1/capabilities").json()

    assert capabilities["search"]["provider"] == "searxng"
    assert capabilities["scholarly"]["unpaywall"] == {
        "enabled": False,
        "disabled_reason": "contact_email_missing",
    }
    assert capabilities["task_backend"]["enabled"] is False


def test_task_backend_is_discovered_from_one_http_url(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        deployment_id="homelab",
        task_backend_url="redis://127.0.0.1:6379/0",
        task_backend_public_port=8090,
    )
    with TestClient(_test_app(settings)) as client:
        capabilities = client.get("/v1/capabilities").json()
        session = client.post("/v1/task-backend/session", json={"agent_id": "alice-desktop"})

    assert capabilities["task_backend"] == {
        "enabled": True,
        "backend": "valkey",
        "scheme": "redis",
        "host": "testserver",
        "port": 8090,
        "database": 0,
        "auth_mode": "none",
        "session_path": "/v1/task-backend/session",
        "deployment_id": "homelab",
        "queue_prefix": "clio-web-homelab",
    }
    assert session.status_code == 200
    assert session.json()["queue_name"].startswith("clio-web-homelab-")
    assert "password" not in session.json()


def test_secure_task_discovery_rejects_missing_bearer(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        task_backend_url="redis://127.0.0.1:6379/0",
        task_backend_api_token="secret-token",  # noqa: S106 - test-only credential
        task_backend_credential_secret="credential-secret",  # noqa: S106
    )
    with TestClient(_test_app(settings)) as client:
        response = client.post("/v1/task-backend/session", json={"agent_id": "alice-desktop"})

    assert response.status_code == 401
    assert response.json()["code"] == "task_backend_unauthorized"
    assert response.json()["remediation"]


def test_readiness_requires_search_and_grobid(tmp_path: Path) -> None:
    response = AsyncMock()
    response.status_code = 200
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)):
        with TestClient(_test_app(_settings(tmp_path))) as client:
            ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json()["checks"] == {
        "docling": "ready",
        "searxng": "ready",
        "grobid": "ready",
    }


def test_document_limit_returns_typed_error(tmp_path: Path) -> None:
    with TestClient(_test_app(_settings(tmp_path, max_input_bytes=4))) as client:
        response = client.post(
            "/v1/documents",
            files={"file": ("large.md", b"12345", "text/markdown")},
        )

    assert response.status_code == 413
    assert response.json()["code"] == "document_too_large"


def test_markdown_conversion_is_durable_and_content_deduplicated(tmp_path: Path) -> None:
    markdown = b"# Durable document\n\nThis content is converted once.\n"
    with TestClient(_test_app(_settings(tmp_path))) as client:
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
        first_page = client.get(
            f"/v1/documents/{job_id}/events", params={"after_sequence": 0, "limit": 2}
        ).json()
        assert first_page["conversion_id"] == job_id
        assert [event["stage"] for event in first_page["events"]] == [
            "queued",
            "starting",
        ]
        second_page = client.get(
            f"/v1/documents/{job_id}/events",
            params={"after_sequence": first_page["next_sequence"], "limit": 100},
        ).json()
        assert second_page["events"][-1]["stage"] == "complete"
        assert second_page["next_sequence"] > first_page["next_sequence"]

        duplicate = client.post(
            "/v1/documents",
            files={"file": ("renamed.md", markdown, "text/markdown")},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == job_id
        assert duplicate.json()["status"] == "complete"


def test_unknown_conversion_has_typed_error(tmp_path: Path) -> None:
    with TestClient(_test_app(_settings(tmp_path))) as client:
        response = client.get("/v1/documents/not-present")

    assert response.status_code == 404
    assert response.json()["code"] == "conversion_not_found"


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_terminal_content_job_is_requeued_on_resubmission(
    tmp_path: Path, terminal_status: str
) -> None:
    """Failed and explicitly cancelled content can be submitted again."""

    markdown = b"# Retry me\n"
    with TestClient(_test_app(_settings(tmp_path))) as client:
        submitted = client.post(
            "/v1/documents",
            files={"file": ("retry.md", markdown, "text/markdown")},
        ).json()
        job_id = submitted["id"]
        deadline = time.monotonic() + 30
        status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status = client.get(f"/v1/documents/{job_id}").json()
            if status["status"] == "complete":
                break
            time.sleep(0.1)
        assert status["status"] == "complete"

        import sqlite3

        with sqlite3.connect(tmp_path / "jobs.sqlite3") as database:
            database.execute(
                "UPDATE jobs SET status = ?, error = 'transient' WHERE id = ?",
                (terminal_status, job_id),
            )
            database.commit()

        retried = client.post(
            "/v1/documents",
            files={"file": ("retry.md", markdown, "text/markdown")},
        )
        assert retried.status_code == 202
        assert retried.json() == {"id": job_id, "status": "queued", "retry_after_s": 2}


def test_public_conversion_failure_is_typed_and_descriptive() -> None:
    failure = public_failure(
        RuntimeError("foreign secret that must stay in logs"),
        stage="docling",
        conversion_id="conversion-1",
    )

    assert failure["code"] == "document_conversion_failed"
    assert failure["retryable"] is True
    assert failure["stage"] == "docling"
    assert failure["conversion_id"] == "conversion-1"
    assert failure["remediation"]
    assert len(failure["message"]) <= 800
    assert "foreign secret" not in failure["message"]


def test_expired_completed_job_is_pruned_on_restart(tmp_path: Path) -> None:
    """Expired job state and owned files are removed when the service starts."""

    markdown = b"# Expire me\n"
    settings = _settings(tmp_path, cache_ttl_days=1)
    with TestClient(_test_app(settings)) as client:
        submitted = client.post(
            "/v1/documents",
            files={"file": ("old.md", markdown, "text/markdown")},
        ).json()
        deadline = time.monotonic() + 30
        status: dict[str, Any] = {}
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

    with TestClient(_test_app(settings)) as client:
        assert client.get(f"/v1/documents/{submitted['id']}").status_code == 404
    assert not Path(row[0]).exists()
    assert not Path(row[1]).exists()


def test_startup_warms_docling_before_readiness(tmp_path: Path) -> None:
    """Application startup does not finish until its conversion worker is ready."""

    starts_before = _TestWorker.starts
    tested_app = _test_app(_settings(tmp_path))
    with TestClient(tested_app):
        assert _TestWorker.starts == starts_before + 1
        assert tested_app.state.queue.ready is True


def test_running_conversion_can_be_cancelled_and_worker_is_rewarmed(tmp_path: Path) -> None:
    """Cancellation persists state and replaces the terminated worker."""

    settings = _settings(tmp_path)
    starts_before = _BlockingWorker.starts
    tested_app = create_app(settings, worker_factory=_BlockingWorker)
    with TestClient(tested_app) as client:
        submitted = client.post(
            "/v1/documents",
            files={"file": ("slow.md", b"# slow", "text/markdown")},
        ).json()
        deadline = time.monotonic() + 5
        state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            state = client.get(f"/v1/documents/{submitted['id']}").json()
            if state["status"] == "running":
                break
            time.sleep(0.01)
        assert state["status"] == "running"

        cancelled = client.post(f"/v1/documents/{submitted['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _BlockingWorker.starts == starts_before + 1:
            time.sleep(0.01)
        assert _BlockingWorker.starts == starts_before + 2
        assert tested_app.state.queue.ready is True
