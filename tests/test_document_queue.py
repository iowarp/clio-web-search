"""Admission, ordering, and cache-accounting tests for the durable queue."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from clio_web_search.config import Settings
from clio_web_search.docling_worker import ConversionCancelledError, ProgressCallback
from clio_web_search.documents import DocumentQueue


class _IdleWorker:
    """Conversion worker that satisfies the protocol without running work."""

    def __init__(self) -> None:
        self._ready = False

    @property
    def ready(self) -> bool:
        """Return whether this worker completed its warmup."""

        return self._ready

    async def start(self) -> None:
        """Mark the worker warm."""

        self._ready = True

    async def convert(
        self,
        path: Path,
        *,
        cancelled: asyncio.Event,
        on_progress: ProgressCallback,
        heartbeat_s: float,
    ) -> dict[str, Any]:
        """Refuse to convert; queue-admission tests never claim work."""

        del path, on_progress, heartbeat_s, cancelled
        raise ConversionCancelledError

    async def stop(self) -> None:
        """Mark the worker stopped."""

        self._ready = False


async def _idle_queue(tmp_path: Path, **overrides: Any) -> DocumentQueue:
    """Return a started-then-stopped queue whose database is ready for direct use."""

    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "searxng_url": "http://127.0.0.1:9",
        "grobid_url": "http://127.0.0.1:9",
        "progress_interval_s": 0.01,
    }
    values.update(overrides)
    queue = DocumentQueue(Settings(**values), worker_factory=_IdleWorker)
    await queue.start()
    await queue.close()
    return queue


def _complete(database_path: Path, job_id: str, result_path: Path) -> None:
    """Mark one job complete with a persisted result file."""

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text('{"markdown": "cached"}', encoding="utf-8")
    with sqlite3.connect(database_path) as database:
        database.execute(
            "UPDATE jobs SET status = 'complete', result_path = ?, progress = 100 WHERE id = ?",
            (str(result_path), job_id),
        )
        database.commit()


@pytest.mark.asyncio
async def test_forced_reprocess_respects_queue_backpressure(tmp_path: Path) -> None:
    """A forced reprocess is admitted only when the queue has capacity."""

    queue = await _idle_queue(tmp_path, max_pending_jobs=1)
    cached = await queue.submit(
        b"# cached\n",
        filename="cached.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
    )
    _complete(
        queue.settings.database_path,
        str(cached["id"]),
        queue.settings.results_dir / f"{cached['id']}.json",
    )
    pending = await queue.submit(
        b"# pending\n",
        filename="pending.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
    )
    assert pending["status"] == "queued"

    forced = await queue.submit(
        b"# cached\n",
        filename="cached.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
        force=True,
    )

    assert forced == {"status": "queue_full", "retry_after_s": 30}
    with sqlite3.connect(queue.settings.database_path) as database:
        status = database.execute(
            "SELECT status FROM jobs WHERE id = ?", (cached["id"],)
        ).fetchone()
    assert status[0] == "complete"


@pytest.mark.asyncio
async def test_forced_reprocess_does_not_preempt_newer_queued_work(tmp_path: Path) -> None:
    """Requeueing refreshes queue ordering so older cache entries wait their turn."""

    queue = await _idle_queue(tmp_path)
    cached = await queue.submit(
        b"# cached\n",
        filename="cached.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
    )
    _complete(
        queue.settings.database_path,
        str(cached["id"]),
        queue.settings.results_dir / f"{cached['id']}.json",
    )
    newer = await queue.submit(
        b"# newer\n",
        filename="newer.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
    )
    now = time.time()
    with sqlite3.connect(queue.settings.database_path) as database:
        database.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (now - 10, cached["id"]))
        database.execute("UPDATE jobs SET created_at = ? WHERE id = ?", (now - 5, newer["id"]))
        database.commit()

    await queue.submit(
        b"# cached\n",
        filename="cached.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
        force=True,
    )

    with sqlite3.connect(queue.settings.database_path) as database:
        claimed = database.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
    assert claimed[0] == newer["id"]


@pytest.mark.asyncio
async def test_forced_reprocess_releases_the_previous_result_file(tmp_path: Path) -> None:
    """The superseded result file leaves disk with its cache-accounting reference."""

    queue = await _idle_queue(tmp_path)
    cached = await queue.submit(
        b"# cached\n",
        filename="cached.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
    )
    result_path = queue.settings.results_dir / f"{cached['id']}.json"
    _complete(queue.settings.database_path, str(cached["id"]), result_path)
    assert result_path.exists()

    await queue.submit(
        b"# cached\n",
        filename="cached.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
        force=True,
    )

    assert not result_path.exists()


@pytest.mark.asyncio
async def test_completed_cache_entry_is_served_while_the_queue_is_full(tmp_path: Path) -> None:
    """Backpressure never hides an already converted result from its requester."""

    queue = await _idle_queue(tmp_path, max_pending_jobs=1)
    cached = await queue.submit(
        b"# cached\n",
        filename="cached.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
    )
    _complete(
        queue.settings.database_path,
        str(cached["id"]),
        queue.settings.results_dir / f"{cached['id']}.json",
    )
    await queue.submit(
        b"# pending\n",
        filename="pending.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
    )

    replayed = await queue.submit(
        b"# cached\n",
        filename="cached.md",
        content_type="text/markdown",
        source_url=None,
        doi=None,
    )

    assert replayed["status"] == "complete"
    assert replayed["result"] == {"markdown": "cached"}
