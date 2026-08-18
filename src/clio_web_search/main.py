"""HTTP gateway for SearXNG, DOI resolution, and document conversion."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from clio_web_search import __version__
from clio_web_search.config import Settings
from clio_web_search.docling_worker import ConversionWorker, DoclingProcessWorker
from clio_web_search.documents import DocumentQueue
from clio_web_search.doi import normalize_doi, resolve_doi
from clio_web_search.errors import error_response
from clio_web_search.task_backend import (
    InvalidAgentIdError,
    TaskBackendAuthorizationError,
    TaskBackendError,
    TaskBackendManager,
)

app_logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    worker_factory: Callable[[], ConversionWorker] = DoclingProcessWorker,
) -> FastAPI:
    """Create one configured CLIO Web Search application."""

    configured = settings or Settings()
    queue = DocumentQueue(configured, worker_factory=worker_factory)
    task_backend = TaskBackendManager(configured)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        await queue.start()
        try:
            yield
        finally:
            await queue.close()

    app = FastAPI(title="CLIO Web Search", version=__version__, lifespan=lifespan)
    app.state.settings = configured
    app.state.queue = queue
    app.state.task_backend = task_backend

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def ready() -> Response:
        checks: dict[str, str] = {"docling": "ready" if queue.ready else "unavailable"}
        if task_backend.enabled:
            checks["valkey"] = "ready" if await task_backend.ready() else "unavailable"
        async with httpx.AsyncClient(timeout=3.0) as client:
            for name, url in {
                "searxng": f"{configured.searxng_url.rstrip('/')}/config",
                "grobid": f"{configured.grobid_url.rstrip('/')}/api/isalive",
            }.items():
                try:
                    response = await client.get(url)
                    checks[name] = "ready" if response.status_code == 200 else "unavailable"
                except httpx.HTTPError:
                    checks[name] = "unavailable"
        status = 200 if all(value == "ready" for value in checks.values()) else 503
        return JSONResponse(
            status_code=status,
            content={"status": "ready" if status == 200 else "not_ready", "checks": checks},
        )

    @app.get("/v1/capabilities")
    async def capabilities(request: Request) -> dict[str, Any]:
        return {
            "service": "clio-web-search",
            "version": __version__,
            "search": {"provider": "searxng", "path": "/search"},
            "documents": {
                "formats": [
                    "pdf",
                    "docx",
                    "pptx",
                    "xlsx",
                    "html",
                    "markdown",
                    "text",
                    "xml",
                    "images",
                ],
                "extractors": ["docling-2.119.0", "grobid-0.9.0-crf"],
                "max_input_bytes": configured.max_input_bytes,
                "queue": await queue.counts(),
                "overall_conversion_timeout": None,
            },
            "task_backend": task_backend.descriptor(request.url.hostname),
            "scholarly": {
                "datacite_search": True,
                "doi_resolution": True,
                "unpaywall": {
                    "enabled": bool(configured.contact_email),
                    "disabled_reason": (
                        None if configured.contact_email else "contact_email_missing"
                    ),
                },
                "openalex_api_key_configured": bool(configured.openalex_api_key),
            },
        }

    @app.post("/v1/task-backend/session")
    async def task_backend_session(
        request: Request,
        payload: dict[str, Any],
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        agent_id = str(payload.get("agent_id", ""))
        try:
            session = await task_backend.issue_session(
                agent_id=agent_id,
                authorization=authorization,
                request_host=request.url.hostname,
            )
        except TaskBackendAuthorizationError as exc:
            return error_response(
                401,
                "task_backend_unauthorized",
                str(exc),
                stage="task_backend_discovery",
                remediation="Provide the configured bearer token and retry discovery.",
            )
        except InvalidAgentIdError as exc:
            return error_response(
                422,
                "invalid_agent_id",
                str(exc),
                stage="task_backend_discovery",
                remediation="Use the locally persisted CLIO Web MCP agent identifier.",
            )
        except TaskBackendError as exc:
            return error_response(
                503,
                "task_backend_unavailable",
                str(exc),
                retryable=True,
                stage="task_backend_discovery",
                remediation="Check Valkey readiness and the deployment task-backend settings.",
            )
        return JSONResponse(content=session)

    @app.post("/v1/doi/resolve")
    async def doi_resolve(payload: dict[str, Any]) -> Response:
        try:
            doi = normalize_doi(str(payload.get("doi", "")))
        except ValueError as exc:
            return error_response(422, "invalid_doi", str(exc))
        return JSONResponse(content=await resolve_doi(doi, configured))

    @app.post("/v1/documents")
    async def submit_document(
        file: Annotated[UploadFile, File()],
        source_url: Annotated[str | None, Form()] = None,
        doi: Annotated[str | None, Form()] = None,
    ) -> Response:
        data = await file.read(configured.max_input_bytes + 1)
        if len(data) > configured.max_input_bytes:
            return error_response(
                413,
                "document_too_large",
                f"The document exceeds the {configured.max_input_bytes}-byte conversion limit.",
                stage="input_validation",
                remediation="Provide a smaller document or raise the configured input limit.",
            )
        safe_name = Path(file.filename or "document").name
        result = await queue.submit(
            data,
            filename=safe_name,
            content_type=file.content_type,
            source_url=source_url,
            doi=doi,
        )
        if result["status"] == "queue_full":
            return error_response(
                429,
                "queue_full",
                "The document conversion queue is full.",
                retryable=True,
                stage="admission",
                remediation="Wait for an active conversion to finish, then retry.",
            )
        status_code = 200 if result["status"] == "complete" else 202
        return JSONResponse(status_code=status_code, content=result)

    @app.get("/v1/documents/{job_id}")
    async def get_document(job_id: str) -> Response:
        result = await queue.get(job_id)
        if result is None:
            return error_response(
                404,
                "conversion_not_found",
                "The requested document conversion does not exist or has expired.",
                stage="lookup",
                remediation="Submit the document again to obtain a current conversion ID.",
                conversion_id=job_id,
            )
        status_code = 200 if result["status"] in {"complete", "failed", "cancelled"} else 202
        return JSONResponse(status_code=status_code, content=result)

    @app.get("/v1/documents/{job_id}/events")
    async def get_document_events(
        job_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> Response:
        if await queue.get(job_id, include_events=False) is None:
            return error_response(
                404,
                "conversion_not_found",
                "The requested document conversion does not exist or has expired.",
                stage="event_lookup",
                remediation="Submit the document again to obtain a current conversion ID.",
                conversion_id=job_id,
            )
        events = await queue.events(job_id, after_sequence=after_sequence, limit=limit)
        return JSONResponse(
            content={
                "conversion_id": job_id,
                "events": events,
                "next_sequence": events[-1]["sequence"] if events else after_sequence,
            }
        )

    @app.post("/v1/documents/{job_id}/cancel")
    async def cancel_document(job_id: str) -> Response:
        result = await queue.cancel(job_id)
        if result is None:
            return error_response(
                404,
                "conversion_not_found",
                "The requested document conversion does not exist or has expired.",
                stage="cancellation",
                remediation="Check the conversion ID before retrying cancellation.",
                conversion_id=job_id,
            )
        return JSONResponse(content=result)

    @app.api_route("/search", methods=["GET", "POST"])
    async def search_proxy(request: Request) -> Response:
        return await _proxy(request, configured, "/search")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
    async def searxng_proxy(request: Request, path: str) -> Response:
        return await _proxy(request, configured, f"/{path}")

    return app


async def _proxy(request: Request, settings: Settings, path: str) -> Response:
    target = f"{settings.searxng_url.rstrip('/')}{path}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection"}
    }
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_s) as client:
            response = await client.request(
                request.method,
                target,
                params=request.query_params,
                content=await request.body(),
                headers=headers,
            )
    except httpx.HTTPError as exc:
        app_logger.warning("SearXNG proxy request failed", exc_info=exc)
        return error_response(
            502,
            "searxng_unavailable",
            "The configured search service did not answer this request.",
            retryable=True,
            stage="search",
            remediation="Retry shortly; if the failure persists, check SearXNG readiness.",
        )
    response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower()
        not in {"content-encoding", "content-length", "transfer-encoding", "connection"}
    }
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )


app = create_app()


def run() -> None:
    """Run the production HTTP gateway."""

    settings = Settings()
    uvicorn.run("clio_web_search.main:app", host=settings.host, port=settings.port)
