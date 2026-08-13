"""Persistent document-conversion queue."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureV2Options
from docling.document_converter import DocumentConverter, PdfFormatOption

from clio_search.config import Settings
from clio_search.grobid import enrich_pdf, looks_like_pdf

_PIPELINE_VERSION = "docling-2.119.0+grobid-0.9.0-crf+clio-3"
_MAX_PUBLIC_ERROR_CHARS = 800
logger = logging.getLogger(__name__)


def _public_failure(exc: Exception) -> dict[str, Any]:
    """Return a bounded, typed conversion failure for API consumers."""

    message = " ".join(str(exc).split())
    if len(message) > _MAX_PUBLIC_ERROR_CHARS:
        message = f"{message[: _MAX_PUBLIC_ERROR_CHARS - 3]}..."
    return {
        "code": "document_conversion_failed",
        "message": message or type(exc).__name__,
        "retryable": True,
    }


class DocumentQueue:
    """SQLite-backed FIFO conversion queue with content-addressed deduplication."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._tasks: list[asyncio.Task[None]] = []
        self._wake = asyncio.Event()
        self._closing = False

    async def start(self) -> None:
        """Initialize persistent state and start conversion workers."""

        self._closing = False
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
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            await database.execute(
                "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
                (time.time(),),
            )
            await database.commit()
        await self._prune_cache()
        self._tasks = [asyncio.create_task(self._worker()) for _ in range(self.settings.workers)]
        self._wake.set()

    async def close(self) -> None:
        """Stop conversion workers after their current operation."""

        self._closing = True
        self._wake.set()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

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
                if existing["status"] == "failed":
                    await database.execute(
                        """
                        UPDATE jobs
                        SET status = 'queued', error = NULL, result_path = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (time.time(), existing["id"]),
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
                    source_url, doi, size_bytes, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)
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
            await database.commit()
        self._wake.set()
        return {"id": job_id, "status": "queued", "retry_after_s": 2}

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """Return one durable conversion job."""

        async with aiosqlite.connect(self.settings.database_path) as database:
            database.row_factory = aiosqlite.Row
            row = await (
                await database.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            ).fetchone()
        return await self._job_payload(dict(row)) if row is not None else None

    async def counts(self) -> dict[str, int]:
        """Return queue counts grouped by status."""

        values = {"queued": 0, "running": 0, "complete": 0, "failed": 0}
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
            rows = [row for row in all_rows if str(row["status"]) in {"complete", "failed"}]
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

    async def _job_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": row["id"],
            "status": row["status"],
            "size_bytes": row["size_bytes"],
        }
        if row["status"] in {"queued", "running"}:
            payload["retry_after_s"] = 2
        elif row["status"] == "failed":
            try:
                payload["error"] = json.loads(row["error"])
            except (json.JSONDecodeError, TypeError):
                payload["error"] = {
                    "code": "document_conversion_failed",
                    "message": str(row["error"]),
                    "retryable": True,
                }
        elif row["status"] == "complete" and row["result_path"]:
            payload["result"] = json.loads(Path(row["result_path"]).read_text(encoding="utf-8"))
        return payload

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
                "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
            await database.commit()
            return dict(row)

    async def _worker(self) -> None:
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
            try:
                result = await asyncio.wait_for(
                    self._convert(job), timeout=self.settings.conversion_timeout_s
                )
                result_path = self.settings.results_dir / f"{job['id']}.json"
                result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                async with aiosqlite.connect(self.settings.database_path) as database:
                    await database.execute(
                        """
                        UPDATE jobs
                        SET status = 'complete', result_path = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (str(result_path), time.time(), job["id"]),
                    )
                    await database.commit()
            except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                logger.exception("Document conversion failed for job %s", job["id"])
                async with aiosqlite.connect(self.settings.database_path) as database:
                    await database.execute(
                        "UPDATE jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(_public_failure(exc)), time.time(), job["id"]),
                    )
                    await database.commit()

    async def _convert(self, job: dict[str, Any]) -> dict[str, Any]:
        path = Path(job["input_path"])
        artifacts_value = os.environ.get("DOCLING_ARTIFACTS_PATH")
        pdf_options = PdfPipelineOptions(
            artifacts_path=Path(artifacts_value) if artifacts_value else None,
            table_structure_options=TableStructureV2Options(),
        )
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)}
        )
        conversion = await asyncio.to_thread(converter.convert, path)
        document = conversion.document
        markdown = document.export_to_markdown()
        document_dict = document.export_to_dict()
        warnings: list[dict[str, str]] = []
        metadata: dict[str, Any] = {}
        references: list[dict[str, Any]] = []
        citation_contexts: list[dict[str, Any]] = []
        profile = "general"
        extractors = [{"name": "docling", "version": "2.119.0"}]
        data = path.read_bytes()
        if looks_like_pdf(data[:16], job["filename"], job["content_type"]):
            try:
                scholarly = await enrich_pdf(
                    path, self.settings, force_scholarly=bool(job.get("doi"))
                )
                profile = str(scholarly.get("profile", "general"))
                metadata.update(scholarly.get("metadata", {}))
                references = scholarly.get("references", [])
                citation_contexts = scholarly.get("citation_contexts", [])
                extractors.append({"name": "grobid", "version": "0.9.0-crf"})
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                warnings.append({"code": "grobid_enrichment_unavailable", "message": str(exc)})
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
