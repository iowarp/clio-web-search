"""HTTP gateway for SearXNG, DOI resolution, and document conversion."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import httpx
import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from clio_search import __version__
from clio_search.config import Settings
from clio_search.documents import DocumentQueue
from clio_search.doi import normalize_doi, resolve_doi
from clio_search.errors import error_response


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create one configured CLIO Search application."""

    configured = settings or Settings()
    queue = DocumentQueue(configured)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        await queue.start()
        yield
        await queue.close()

    app = FastAPI(title="CLIO Search", version=__version__, lifespan=lifespan)
    app.state.settings = configured
    app.state.queue = queue

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def ready() -> Response:
        checks: dict[str, str] = {"queue": "ready"}
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
    async def capabilities() -> dict[str, Any]:
        return {
            "service": "clio-search",
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
            },
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
                f"document exceeds {configured.max_input_bytes} bytes",
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
            return error_response(429, "queue_full", "document queue is full", retryable=True)
        status_code = 200 if result["status"] == "complete" else 202
        return JSONResponse(status_code=status_code, content=result)

    @app.get("/v1/documents/{job_id}")
    async def get_document(job_id: str) -> Response:
        result = await queue.get(job_id)
        if result is None:
            return error_response(404, "conversion_not_found", "conversion does not exist")
        status_code = 200 if result["status"] in {"complete", "failed"} else 202
        return JSONResponse(status_code=status_code, content=result)

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
        return error_response(502, "searxng_unavailable", str(exc), retryable=True)
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
    uvicorn.run("clio_search.main:app", host=settings.host, port=settings.port)
