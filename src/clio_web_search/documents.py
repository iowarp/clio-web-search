"""Persistent document-conversion queue."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite
import httpx

from clio_web_search.config import Settings
from clio_web_search.docling_worker import (
    ConversionCancelledError,
    ConversionWorker,
    DoclingProcessWorker,
)
from clio_web_search.grobid import enrich_pdf, looks_like_pdf

_PIPELINE_VERSION = "docling-2.119.0+grobid-0.9.0-crf+clio-4"
_MAX_PUBLIC_ERROR_CHARS = 800
logger = logging.getLogger(__name__)


def public_failure(exc: Exception, *, stage: str, conversion_id: str) -> dict[str, Any]:
    """Return a bounded agent-facing failure without leaking foreign exception text."""

    if isinstance(exc, MemoryError):
        message = "Document conversion exhausted the worker's available memory."
        remediation = "Retry with a smaller document or increase the container memory limit."
        retryable = False
        code = "document_conversion_out_of_memory"
    elif isinstance(exc, OSError):
        message = "Document conversion could not read or persist an input or result file."
        remediation = "Check free space and permissions on the CLIO Web Search data volume."
        retryable = True
        code = "document_conversion_storage_error"
    elif isinstance(exc, ValueError | KeyError | TypeError):
        message = "The document could not be interpreted as a supported conversion input."
        remediation = "Verify the file is not corrupt or password-protected, then submit it again."
        retryable = False
        code = "document_conversion_invalid_input"
    else:
        message = "The document pipeline stopped unexpectedly before producing a result."
        remediation = (
            "Retry once; if it fails again, query the conversion event log and inspect the "
            "server log using this conversion ID."
        )
        retryable = True
        code = "document_conversion_failed"
    return {
        "code": code,
        "stage": stage,
        "message": message[:_MAX_PUBLIC_ERROR_CHARS],
        "retryable": retryable,
        "remediation": remediation,
        "conversion_id": conversion_id,
    }


class DocumentQueue:
    """SQLite-backed FIFO conversion queue with content-addressed deduplication."""

    def __init__(
        self,
        settings: Settings,
        *,
        worker_factory: Callable[[], ConversionWorker] = DoclingProcessWorker,
    ) -> None:
        self.settings = settings
        self._tasks: list[asyncio.Task[None]] = []
        self._workers: list[ConversionWorker] = []
        self._worker_factory = worker_factory
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._wake = asyncio.Event()
        self._closing = False
        self._ready = False

    @property
    def ready(self) -> bool:
        """Return whether every conversion worker is warmed and available."""

        return self._ready and all(worker.ready for worker in self._workers)

    async def start(self) -> None:
        """Initialize state and warm every Docling worker before returning."""

        self._closing = False
        self._ready = False
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.settings.results_dir.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.settings.database_path) as database:
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    cache_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    result_path TEXT,
                    filename TEXT NOT NULL,
                    content_type TEXT,
                    source_url TEXT,
                    doi TEXT,
                    size_bytes INTEGER NOT NULL,
                    error TEXT,
                    progress INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT 'queued',
                    message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            columns = {
                str(row[1])
                for row in await (await database.execute("PRAGMA table_info(jobs)")).fetchall()
            }
            if "progress" not in columns:
                await database.execute(
                    "ALTER TABLE jobs ADD COLUMN progress INTEGER NOT NULL DEFAULT 0"
                )
            if "message" not in columns:
                await database.execute("ALTER TABLE jobs ADD COLUMN message TEXT")
            if "stage" not in columns:
                await database.execute(
                    "ALTER TABLE jobs ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'"
                )
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS job_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    level TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'queued',
                    message TEXT NOT NULL
                )
                """
            )
            event_columns = {
                str(row[1])
                for row in await (
                    await database.execute("PRAGMA table_info(job_events)")
                ).fetchall()
            }
            if "stage" not in event_columns:
                await database.execute(
                    "ALTER TABLE job_events ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'"
                )
            await database.execute(
                "CREATE INDEX IF NOT EXISTS job_events_job_sequence ON job_events(job_id, sequence)"
            )
            restarted_at = time.time()
            await database.execute(
                """
                INSERT INTO job_events (job_id, created_at, level, progress, stage, message)
                SELECT id, ?, 'warning', 0, 'queued', 'Requeued after service restart'
                FROM jobs WHERE status = 'running'
                """,
                (restarted_at,),
            )
            await database.execute(
                """
                UPDATE jobs
                SET status = 'queued', progress = 0, stage = 'queued',
                    message = 'Requeued after service restart', updated_at = ?
                WHERE status = 'running'
                """,
                (restarted_at,),
            )
            await database.commit()
        await self._prune_cache()
        self._workers = [self._worker_factory() for _ in range(self.settings.workers)]
        try:
            await asyncio.gather(*(worker.start() for worker in self._workers))
        except BaseException:
            await asyncio.gather(
                *(worker.stop() for worker in self._workers), return_exceptions=True
            )
            self._workers.clear()
            raise
        self._tasks = [asyncio.create_task(self._worker(worker)) for worker in self._workers]
        self._ready = True
        self._wake.set()

    async def close(self) -> None:
        """Stop queue tasks and terminate any active conversion immediately."""

        self._closing = True
        self._ready = False
        for event in self._cancel_events.values():
            event.set()
        self._wake.set()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await asyncio.gather(*(worker.stop() for worker in self._workers), return_exceptions=True)
        self._workers.clear()

    async def submit(
        self,
        data: bytes,
        *,
        filename: str,
        content_type: str | None,
        source_url: str | None,
        doi: str | None,
    ) -> dict[str, Any]:
        """Create or resume a content-addressed conversion job."""

        digest = hashlib.sha256(data).hexdigest()
        cache_key = hashlib.sha256(f"{digest}:{_PIPELINE_VERSION}".encode()).hexdigest()
        async with aiosqlite.connect(self.settings.database_path) as database:
            database.row_factory = aiosqlite.Row
            existing = await (
                await database.execute("SELECT * FROM jobs WHERE cache_key = ?", (cache_key,))
            ).fetchone()
            if existing is not None:
                if existing["status"] in {"failed", "cancelled"}:
                    now = time.time()
                    await database.execute(
                        """
                        UPDATE jobs
                        SET status = 'queued', error = NULL, result_path = NULL,
                            progress = 0, stage = 'queued',
                            message = 'Queued for retry', updated_at = ?
                        WHERE id = ?
                        """,
                        (now, existing["id"]),
                    )
                    await database.execute(
                        """
                        INSERT INTO job_events
                            (job_id, created_at, level, progress, stage, message)
                        VALUES (?, ?, 'info', 0, 'queued', 'Queued for retry')
                        """,
                        (existing["id"], now),
                    )
                    await database.commit()
                    self._wake.set()
                    return {"id": existing["id"], "status": "queued", "retry_after_s": 2}
                return await self._job_payload(dict(existing))
            pending = await (
                await database.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued', 'running')"
                )
            ).fetchone()
            if pending is not None and int(pending[0]) >= self.settings.max_pending_jobs:
                return {"status": "queue_full", "retry_after_s": 30}
            job_id = str(uuid.uuid4())
            suffix = Path(filename).suffix[:16]
            input_path = self.settings.uploads_dir / f"{digest}{suffix}"
            if not input_path.exists():
                input_path.write_bytes(data)
            now = time.time()
            await database.execute(
                """
                INSERT INTO jobs (
                    id, cache_key, status, input_path, filename, content_type,
                    source_url, doi, size_bytes, progress, stage, message, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, 0, 'queued', 'Queued', ?, ?)
                """,
                (
                    job_id,
                    cache_key,
                    str(input_path),
                    filename,
                    content_type,
                    source_url,
                    doi,
                    len(data),
                    now,
                    now,
                ),
            )
            await database.execute(
                """
                INSERT INTO job_events (job_id, created_at, level, progress, stage, message)
                VALUES (?, ?, 'info', 0, 'queued', 'Queued')
                """,
                (job_id, now),
            )
            await database.commit()
        self._wake.set()
        return {"id": job_id, "status": "queued", "retry_after_s": 2}

    async def get(self, job_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        """Return one durable conversion job."""

        async with aiosqlite.connect(self.settings.database_path) as database:
            database.row_factory = aiosqlite.Row
            row = await (
                await database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            ).fetchone()
        return (
            await self._job_payload(dict(row), include_events=include_events)
            if row is not None
            else None
        )

    async def events(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return an ordered page of persistent progress and log events."""

        bounded_limit = min(max(limit, 1), 500)
        async with aiosqlite.connect(self.settings.database_path) as database:
            database.row_factory = aiosqlite.Row
            rows = await (
                await database.execute(
                    """
                    SELECT sequence, created_at, level, progress, stage, message
                    FROM job_events
                    WHERE job_id = ? AND sequence > ?
                    ORDER BY sequence
                    LIMIT ?
                    """,
                    (job_id, after_sequence, bounded_limit),
                )
            ).fetchall()
        return [dict(row) for row in rows]

    async def recent_events(self, job_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent persistent events in chronological order."""

        bounded_limit = min(max(limit, 1), 500)
        async with aiosqlite.connect(self.settings.database_path) as database:
            database.row_factory = aiosqlite.Row
            rows = await (
                await database.execute(
                    """
                    SELECT sequence, created_at, level, progress, stage, message
                    FROM (
                        SELECT sequence, created_at, level, progress, stage, message
                        FROM job_events WHERE job_id = ?
                        ORDER BY sequence DESC LIMIT ?
                    )
                    ORDER BY sequence
                    """,
                    (job_id, bounded_limit),
                )
            ).fetchall()
        return [dict(row) for row in rows]

    async def cancel(self, job_id: str) -> dict[str, Any] | None:
        """Persist cancellation and signal the active conversion worker."""

        now = time.time()
        async with aiosqlite.connect(self.settings.database_path) as database:
            database.row_factory = aiosqlite.Row
            await database.execute("BEGIN IMMEDIATE")
            row = await (
                await database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            ).fetchone()
            if row is None:
                await database.rollback()
                return None
            if str(row["status"]) in {"queued", "running"}:
                await database.execute(
                    """
                    UPDATE jobs
                    SET status = 'cancelled', stage = 'cancelled',
                        message = 'Cancelled by requester', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, job_id),
                )
                await database.execute(
                    """
                    INSERT INTO job_events
                        (job_id, created_at, level, progress, stage, message)
                    VALUES (?, ?, 'warning', ?, 'cancelled', 'Cancelled by requester')
                    """,
                    (job_id, now, int(row["progress"] or 0)),
                )
                await database.commit()
                event = self._cancel_events.get(job_id)
                if event is not None:
                    event.set()
                self._wake.set()
            else:
                await database.rollback()
        return await self.get(job_id)

    async def counts(self) -> dict[str, int]:
        """Return queue counts grouped by status."""

        values = {"queued": 0, "running": 0, "complete": 0, "failed": 0, "cancelled": 0}
        async with aiosqlite.connect(self.settings.database_path) as database:
            rows = await (
                await database.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
            ).fetchall()
        for status, count in rows:
            values[str(status)] = int(count)
        return values

    async def _prune_cache(self) -> None:
        """Remove expired completed jobs, then enforce the configured byte budget."""

        cutoff = time.time() - (self.settings.cache_ttl_days * 86_400)
        async with aiosqlite.connect(self.settings.database_path) as database:
            database.row_factory = aiosqlite.Row
            all_rows = await (
                await database.execute("SELECT * FROM jobs ORDER BY updated_at ASC")
            ).fetchall()
            rows = [
                row for row in all_rows if str(row["status"]) in {"complete", "failed", "cancelled"}
            ]
            retained_bytes = sum(
                self._job_disk_bytes(dict(row))
                for row in rows
                if float(row["updated_at"]) >= cutoff
            )
            delete_ids: list[str] = []
            delete_jobs: list[dict[str, Any]] = []
            for row in rows:
                job = dict(row)
                expired = float(row["updated_at"]) < cutoff
                over_budget = retained_bytes > self.settings.cache_max_bytes
                if not expired and not over_budget:
                    continue
                delete_ids.append(str(row["id"]))
                delete_jobs.append(job)
                if not expired:
                    retained_bytes -= self._job_disk_bytes(job)
            retained_inputs = {
                str(row["input_path"]) for row in all_rows if str(row["id"]) not in delete_ids
            }
            for job in delete_jobs:
                self._unlink_job_files(job, retained_inputs=retained_inputs)
            if delete_ids:
                await database.executemany(
                    "DELETE FROM job_events WHERE job_id = ?",
                    [(job_id,) for job_id in delete_ids],
                )
                await database.executemany(
                    "DELETE FROM jobs WHERE id = ?",
                    [(job_id,) for job_id in delete_ids],
                )
                await database.commit()

    @staticmethod
    def _job_disk_bytes(job: dict[str, Any]) -> int:
        """Return the size of one job's persisted input and result files."""

        total = 0
        for key in ("input_path", "result_path"):
            value = job.get(key)
            if value:
                try:
                    total += Path(value).stat().st_size
                except OSError:
                    pass
        return total

    @staticmethod
    def _unlink_job_files(job: dict[str, Any], *, retained_inputs: set[str]) -> None:
        """Delete one cached job's owned input and result files if present."""

        for key in ("input_path", "result_path"):
            value = job.get(key)
            if key == "input_path" and str(value) in retained_inputs:
                continue
            if value:
                try:
                    Path(value).unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove expired cache file %s", value)

    async def _job_payload(
        self, row: dict[str, Any], *, include_events: bool = True
    ) -> dict[str, Any]:
        """Build one public job payload from persistent state."""

        payload: dict[str, Any] = {
            "id": row["id"],
            "status": row["status"],
            "size_bytes": row["size_bytes"],
            "progress": int(row.get("progress") or 0),
            "stage": row.get("stage") or "unknown",
            "message": row.get("message"),
        }
        if row["status"] in {"queued", "running"}:
            payload["retry_after_s"] = 2
        elif row["status"] == "failed":
            try:
                payload["error"] = json.loads(row["error"])
            except (json.JSONDecodeError, TypeError):
                payload["error"] = {
                    "code": "document_conversion_failed",
                    "stage": row.get("stage") or "unknown",
                    "message": "The document pipeline stopped unexpectedly.",
                    "retryable": True,
                    "remediation": "Retry the conversion and inspect its event log if it repeats.",
                    "conversion_id": row["id"],
                }
        elif row["status"] == "complete" and row["result_path"]:
            payload["result"] = json.loads(Path(row["result_path"]).read_text(encoding="utf-8"))
        if include_events:
            payload["events"] = await self.recent_events(str(row["id"]))
        return payload

    async def _record_event(
        self,
        job_id: str,
        progress: int,
        stage: str,
        message: str,
        level: str = "info",
    ) -> None:
        """Persist a bounded event and update the job's current progress."""

        normalized = " ".join(message.split())[:4000]
        if not normalized:
            return
        bounded_progress = min(max(progress, 0), 100)
        now = time.time()
        async with aiosqlite.connect(self.settings.database_path) as database:
            await database.execute(
                """
                UPDATE jobs
                SET progress = MAX(progress, ?), stage = ?, message = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (bounded_progress, stage, normalized, now, job_id),
            )
            await database.execute(
                """
                INSERT INTO job_events (job_id, created_at, level, progress, stage, message)
                SELECT ?, ?, ?, progress, ?, ?
                FROM jobs WHERE id = ? AND status = 'running'
                """,
                (job_id, now, level, stage, normalized, job_id),
            )
            await database.execute(
                """
                DELETE FROM job_events
                WHERE job_id = ? AND sequence NOT IN (
                    SELECT sequence FROM job_events
                    WHERE job_id = ? ORDER BY sequence DESC LIMIT 500
                )
                """,
                (job_id, job_id),
            )
            await database.commit()

    async def _claim(self) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.settings.database_path) as database:
            database.row_factory = aiosqlite.Row
            await database.execute("BEGIN IMMEDIATE")
            row = await (
                await database.execute(
                    "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
                )
            ).fetchone()
            if row is None:
                await database.rollback()
                return None
            await database.execute(
                """
                UPDATE jobs
                SET status = 'running', progress = 1, stage = 'starting',
                    message = 'Conversion worker started',
                    updated_at = ?
                WHERE id = ?
                """,
                (time.time(), row["id"]),
            )
            await database.execute(
                """
                INSERT INTO job_events (job_id, created_at, level, progress, stage, message)
                VALUES (?, ?, 'info', 1, 'starting', 'Conversion worker started')
                """,
                (row["id"], time.time()),
            )
            await database.commit()
            return dict(row)

    async def _worker(self, converter: ConversionWorker) -> None:
        """Run queued jobs sequentially on one persistent warmed worker."""

        while not self._closing:
            job = await self._claim()
            if job is None:
                if self._closing:
                    return
                self._wake.clear()
                if self._closing:
                    return
                await self._wake.wait()
                continue
            job_id = str(job["id"])
            cancelled = asyncio.Event()
            self._cancel_events[job_id] = cancelled
            try:
                current = await self.get(job_id, include_events=False)
                if current is not None and current["status"] == "cancelled":
                    cancelled.set()
                result = await self._convert(job, converter=converter, cancelled=cancelled)
                result_path = self.settings.results_dir / f"{job['id']}.json"
                result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                async with aiosqlite.connect(self.settings.database_path) as database:
                    updated = await database.execute(
                        """
                        UPDATE jobs
                        SET status = 'complete', result_path = ?, progress = 100,
                            stage = 'complete', message = 'Conversion complete', updated_at = ?
                        WHERE id = ? AND status = 'running'
                        """,
                        (str(result_path), time.time(), job["id"]),
                    )
                    await database.execute(
                        """
                        INSERT INTO job_events
                            (job_id, created_at, level, progress, stage, message)
                        SELECT ?, ?, 'info', 100, 'complete', 'Conversion complete'
                        WHERE EXISTS (SELECT 1 FROM jobs WHERE id = ? AND status = 'complete')
                        """,
                        (job_id, time.time(), job_id),
                    )
                    await database.commit()
                if updated.rowcount == 0:
                    result_path.unlink(missing_ok=True)
            except ConversionCancelledError:
                await converter.stop()
                if not self._closing:
                    await converter.start()
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.exception("Document conversion failed for job %s", job["id"])
                current = await self.get(job_id, include_events=False)
                failure_stage = str(current.get("stage", "conversion")) if current else "conversion"
                failure = public_failure(exc, stage=failure_stage, conversion_id=job_id)
                async with aiosqlite.connect(self.settings.database_path) as database:
                    await database.execute(
                        """
                        UPDATE jobs
                        SET status = 'failed', error = ?, stage = ?, message = ?,
                            updated_at = ?
                        WHERE id = ? AND status = 'running'
                        """,
                        (
                            json.dumps(failure),
                            failure_stage,
                            failure["message"],
                            time.time(),
                            job_id,
                        ),
                    )
                    await database.execute(
                        """
                        INSERT INTO job_events
                            (job_id, created_at, level, progress, stage, message)
                        SELECT ?, ?, 'error', progress, ?, ?
                        FROM jobs WHERE id = ? AND status = 'failed'
                        """,
                        (
                            job_id,
                            time.time(),
                            failure_stage,
                            failure["message"],
                            job_id,
                        ),
                    )
                    await database.commit()
                if not converter.ready and not self._closing:
                    await converter.start()
            finally:
                self._cancel_events.pop(job_id, None)

    async def _convert(
        self,
        job: dict[str, Any],
        *,
        converter: ConversionWorker,
        cancelled: asyncio.Event,
    ) -> dict[str, Any]:
        """Run Docling without an overall deadline, then optionally enrich a PDF."""

        path = Path(job["input_path"])

        async def on_progress(progress: int, stage: str, message: str, level: str) -> None:
            await self._record_event(str(job["id"]), progress, stage, message, level)

        converted = await converter.convert(
            path,
            cancelled=cancelled,
            on_progress=on_progress,
            heartbeat_s=self.settings.progress_interval_s,
        )
        if cancelled.is_set():
            raise ConversionCancelledError
        markdown = str(converted["markdown"])
        document_dict = converted["structure"]
        warnings: list[dict[str, str]] = []
        metadata: dict[str, Any] = {}
        references: list[dict[str, Any]] = []
        citation_contexts: list[dict[str, Any]] = []
        profile = "general"
        extractors = [{"name": "docling", "version": "2.119.0"}]
        data = path.read_bytes()
        if looks_like_pdf(data[:16], job["filename"], job["content_type"]):
            await self._record_event(
                str(job["id"]),
                80,
                "grobid",
                "Requesting scholarly metadata from GROBID",
            )
            try:
                enrichment = asyncio.create_task(
                    enrich_pdf(path, self.settings, force_scholarly=bool(job.get("doi")))
                )
                cancellation = asyncio.create_task(cancelled.wait())
                done, _ = await asyncio.wait(
                    {enrichment, cancellation}, return_when=asyncio.FIRST_COMPLETED
                )
                if cancellation in done and cancelled.is_set():
                    enrichment.cancel()
                    await asyncio.gather(enrichment, return_exceptions=True)
                    raise ConversionCancelledError
                cancellation.cancel()
                await asyncio.gather(cancellation, return_exceptions=True)
                scholarly = await enrichment
                profile = str(scholarly.get("profile", "general"))
                metadata.update(scholarly.get("metadata", {}))
                references = scholarly.get("references", [])
                citation_contexts = scholarly.get("citation_contexts", [])
                extractors.append({"name": "grobid", "version": "0.9.0-crf"})
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                warnings.append(
                    {
                        "code": "grobid_enrichment_unavailable",
                        "message": (
                            "Scholarly metadata enrichment was unavailable; the Docling "
                            "document and Markdown remain complete."
                        ),
                    }
                )
                logger.warning("GROBID enrichment failed for job %s", job["id"], exc_info=exc)
                await self._record_event(
                    str(job["id"]),
                    90,
                    "grobid",
                    "GROBID scholarly enrichment was unavailable; the Docling result is intact.",
                    "warning",
                )
        if cancelled.is_set():
            raise ConversionCancelledError
        if job.get("doi"):
            metadata.setdefault("doi", job["doi"])
        return {
            "markdown": markdown,
            "document": {
                "document_type": job.get("content_type") or path.suffix.lstrip("."),
                "profile": profile,
                "metadata": metadata,
                "structure": document_dict,
                "references": references,
                "citation_contexts": citation_contexts,
                "capabilities": [
                    "markdown",
                    "document_structure",
                    *(["bibliography", "citation_contexts"] if references else []),
                ],
                "warnings": warnings,
                "extractors": extractors,
            },
        }
