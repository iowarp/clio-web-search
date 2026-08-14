"""Typed service errors."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Return the stable CLIO Web Search error envelope."""

    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        },
    )
