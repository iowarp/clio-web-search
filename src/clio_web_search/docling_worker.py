"""Cancellable, eagerly warmed Docling conversion worker processes."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import multiprocessing
import os
import tempfile
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, cast

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureV2Options
from docling.document_converter import DocumentConverter, PdfFormatOption

ProgressCallback = Callable[[int, str, str, str], Awaitable[None]]


class ConversionCancelledError(Exception):
    """Raised when an active conversion is explicitly cancelled."""


class ConversionWorker(Protocol):
    """Interface implemented by one warmed, cancellable conversion worker."""

    @property
    def ready(self) -> bool:
        """Return whether the worker completed Docling warmup."""

        ...

    async def start(self) -> None:
        """Start the worker and wait for Docling warmup to finish."""

        ...

    async def convert(
        self,
        path: Path,
        *,
        cancelled: asyncio.Event,
        on_progress: ProgressCallback,
        heartbeat_s: float,
    ) -> dict[str, Any]:
        """Convert one document, forwarding progress and logs."""

        ...

    async def stop(self) -> None:
        """Stop the worker process."""

        ...


class _ConnectionLike(Protocol):
    """Structural type shared by multiprocessing pipe implementations."""

    def send(self, obj: Any) -> None:
        """Send one picklable value."""

        ...

    def recv(self) -> Any:
        """Receive one value."""

        ...

    def poll(self, timeout: float = 0.0) -> bool:
        """Return whether a value is available within the timeout."""

        ...

    def close(self) -> None:
        """Close this pipe endpoint."""

        ...


class _ProcessLike(Protocol):
    """Structural type shared by multiprocessing process contexts."""

    @property
    def exitcode(self) -> int | None:
        """Return the child exit code when available."""

        ...

    def is_alive(self) -> bool:
        """Return whether the child is running."""

        ...

    def terminate(self) -> None:
        """Terminate the child."""

        ...

    def kill(self) -> None:
        """Force-kill the child."""

        ...

    def join(self, timeout: float | None = None) -> None:
        """Wait for the child to exit."""

        ...


class _PipeTextWriter(io.TextIOBase):
    """Forward child-process stdout or stderr as structured pipe messages."""

    def __init__(self, connection: _ConnectionLike, stream: str) -> None:
        self._connection = connection
        self._stream = stream
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, value: str) -> int:
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._send(line)
        return len(value)

    def flush(self) -> None:
        if self._buffer.strip():
            self._send(self._buffer)
        self._buffer = ""

    def _send(self, line: str) -> None:
        try:
            self._connection.send({"type": "log", "stream": self._stream, "message": line})
        except (BrokenPipeError, EOFError, OSError):
            return


def _build_converter(artifacts_path: str | None) -> DocumentConverter:
    """Construct the configured Docling converter."""

    pdf_options = PdfPipelineOptions(
        artifacts_path=Path(artifacts_path) if artifacts_path else None,
        table_structure_options=TableStructureV2Options(),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)}
    )


def _warmup_pdf() -> bytes:
    """Build a valid one-page PDF used only to exercise the startup pipeline."""

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length 42 >>\nstream\nBT /F1 12 Tf 20 100 Td (warmup) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode())
        document.extend(value)
        document.extend(b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)


def _worker_main(connection: _ConnectionLike, artifacts_path: str | None) -> None:
    """Warm Docling, then execute conversion commands in a child process."""

    stdout = _PipeTextWriter(connection, "stdout")
    stderr = _PipeTextWriter(connection, "stderr")
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            logging.basicConfig(stream=stderr, level=logging.INFO, force=True)
            connection.send(
                {
                    "type": "progress",
                    "progress": 0,
                    "stage": "initialization",
                    "message": "Loading the Docling PDF pipeline",
                }
            )
            converter = _build_converter(artifacts_path)
            converter.initialize_pipeline(InputFormat.PDF)
            with tempfile.TemporaryDirectory(prefix="clio-docling-warmup-") as directory:
                fixture = Path(directory) / "warmup.pdf"
                fixture.write_bytes(_warmup_pdf())
                converter.convert(fixture)
            connection.send(
                {
                    "type": "progress",
                    "progress": 100,
                    "stage": "initialization",
                    "message": "Docling models and PDF pipeline are warm",
                }
            )
            connection.send({"type": "ready"})
            while True:
                command = connection.recv()
                if command.get("type") == "stop":
                    return
                if command.get("type") != "convert":
                    raise RuntimeError("Docling worker received an unknown command")
                path = Path(str(command["path"]))
                try:
                    connection.send(
                        {
                            "type": "progress",
                            "progress": 10,
                            "stage": "docling",
                            "message": f"Docling is processing {path.name}",
                        }
                    )
                    conversion = converter.convert(path)
                    connection.send(
                        {
                            "type": "progress",
                            "progress": 70,
                            "stage": "export",
                            "message": "Docling conversion complete; exporting result",
                        }
                    )
                    result = {
                        "markdown": conversion.document.export_to_markdown(),
                        "structure": conversion.document.export_to_dict(),
                    }
                    connection.send({"type": "result", "result": result})
                except Exception as exc:
                    connection.send(
                        {
                            "type": "error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "traceback": traceback.format_exc(),
                        }
                    )
    except (BrokenPipeError, EOFError):
        return
    except BaseException as exc:
        try:
            connection.send(
                {
                    "type": "fatal",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        stdout.flush()
        stderr.flush()
        connection.close()


class DoclingProcessWorker:
    """One persistent Docling process warmed before application readiness."""

    def __init__(self) -> None:
        self._context = multiprocessing.get_context("spawn")
        self._process: _ProcessLike | None = None
        self._connection: _ConnectionLike | None = None
        self._ready = False

    @property
    def ready(self) -> bool:
        """Return whether the child completed its pipeline warmup."""

        return self._ready and self._process is not None and self._process.is_alive()

    async def start(self) -> None:
        """Spawn the child and wait without a fixed warmup timeout."""

        if self._process is not None:
            await self.stop()
        parent, child = self._context.Pipe()
        process = self._context.Process(
            target=_worker_main,
            args=(child, os.environ.get("DOCLING_ARTIFACTS_PATH")),
            name="clio-docling-worker",
            daemon=True,
        )
        process.start()
        child.close()
        self._process = process
        self._connection = parent
        self._ready = False
        while True:
            message = await self._receive(timeout_s=1.0)
            if message is None:
                if not process.is_alive():
                    raise RuntimeError(
                        f"Docling worker exited during startup with code {process.exitcode}"
                    )
                continue
            message_type = str(message.get("type"))
            if message_type == "ready":
                self._ready = True
                return
            if message_type == "log":
                logging.getLogger(__name__).info("Docling startup: %s", message.get("message"))
            elif message_type == "progress":
                logging.getLogger(__name__).info("%s", message.get("message"))
            elif message_type in {"error", "fatal"}:
                raise RuntimeError(
                    f"Docling worker warmup failed: {message.get('error_type')}: "
                    f"{message.get('message')}"
                )

    async def convert(
        self,
        path: Path,
        *,
        cancelled: asyncio.Event,
        on_progress: ProgressCallback,
        heartbeat_s: float,
    ) -> dict[str, Any]:
        """Convert one document and cooperatively observe cancellation."""

        if not self.ready or self._connection is None or self._process is None:
            raise RuntimeError("Docling worker is not ready")
        self._connection.send({"type": "convert", "path": str(path)})
        elapsed = 0.0
        while True:
            if cancelled.is_set():
                raise ConversionCancelledError
            message = await self._receive(timeout_s=heartbeat_s)
            if message is None:
                if cancelled.is_set():
                    raise ConversionCancelledError
                if not self._process.is_alive():
                    raise RuntimeError(f"Docling worker exited with code {self._process.exitcode}")
                elapsed += heartbeat_s
                await on_progress(
                    40,
                    "docling",
                    f"Docling is still processing ({elapsed:.0f}s elapsed)",
                    "info",
                )
                continue
            message_type = str(message.get("type"))
            if message_type == "progress":
                await on_progress(
                    int(message.get("progress", 0)),
                    str(message.get("stage", "docling")),
                    str(message.get("message", "Docling progress updated")),
                    "info",
                )
            elif message_type == "log":
                await on_progress(
                    40,
                    "docling",
                    str(message.get("message", "")),
                    "warning" if message.get("stream") == "stderr" else "info",
                )
            elif message_type == "result":
                value = message.get("result")
                if not isinstance(value, dict):
                    raise RuntimeError("Docling worker returned an invalid result")
                return cast(dict[str, Any], value)
            elif message_type in {"error", "fatal"}:
                detail = str(message.get("traceback", ""))
                if detail:
                    logging.getLogger(__name__).error("Docling worker failed:\n%s", detail)
                raise RuntimeError(
                    f"{message.get('error_type', 'DoclingError')}: {message.get('message', '')}"
                )

    async def stop(self) -> None:
        """Terminate the child immediately so active conversion can be cancelled."""

        self._ready = False
        connection, self._connection = self._connection, None
        process, self._process = self._process, None
        if process is not None and process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 10.0)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join)
        elif process is not None:
            await asyncio.to_thread(process.join)
        if connection is not None:
            connection.close()

    async def _receive(self, *, timeout_s: float) -> dict[str, Any] | None:
        """Receive one child message, returning ``None`` on a poll timeout."""

        connection = self._connection
        if connection is None:
            raise RuntimeError("Docling worker connection is closed")
        available = await asyncio.to_thread(connection.poll, timeout_s)
        if not available:
            return None
        try:
            value = connection.recv()
        except EOFError as exc:
            raise RuntimeError("Docling worker connection closed unexpectedly") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Docling worker sent an invalid message")
        return cast(dict[str, Any], value)

    async def __aenter__(self) -> DoclingProcessWorker:
        """Start this worker for use as an async context manager."""

        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback_value: TracebackType | None,
    ) -> None:
        """Stop this worker when its async context exits."""

        await self.stop()
