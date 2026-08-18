"""Valkey discovery, readiness, and scoped Docket credential bootstrap."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from pathlib import Path
from typing import Any, cast

from redis.asyncio import Redis

from clio_web_search.config import Settings

_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class TaskBackendError(Exception):
    """Base class for task-backend discovery failures."""


class TaskBackendAuthorizationError(TaskBackendError):
    """Raised when secure task-backend discovery is not authorized."""


class InvalidAgentIdError(TaskBackendError):
    """Raised when a caller supplies an unsafe agent identifier."""


class TaskBackendManager:
    """Manage the Valkey endpoint used by agent-local FastMCP Docket workers."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        """Return whether a Valkey task backend is configured."""

        return self.settings.task_backend_url is not None

    @property
    def auth_mode(self) -> str:
        """Return the advertised task-backend authentication mode."""

        return "session" if self.settings.task_backend_api_token is not None else "none"

    async def ready(self) -> bool:
        """Return whether the configured Valkey endpoint answers ``PING``."""

        if not self.enabled:
            return False
        client = self._client()
        try:
            return bool(
                await client.ping()  # pyright: ignore[reportUnknownMemberType]
            )
        except Exception:
            return False
        finally:
            await client.aclose()

    def descriptor(self, request_host: str | None) -> dict[str, Any]:
        """Return public connection metadata without exposing credentials."""

        host = self.settings.task_backend_public_host or request_host
        return {
            "enabled": self.enabled,
            "backend": "valkey" if self.enabled else None,
            "scheme": "rediss" if self.settings.task_backend_tls else "redis",
            "host": host,
            "port": self.settings.task_backend_public_port,
            "database": self.settings.task_backend_database,
            "auth_mode": self.auth_mode if self.enabled else None,
            "session_path": "/v1/task-backend/session" if self.enabled else None,
            "deployment_id": self.settings.deployment_id,
            "queue_prefix": self._queue_prefix(),
        }

    async def issue_session(
        self,
        *,
        agent_id: str,
        authorization: str | None,
        request_host: str | None,
    ) -> dict[str, Any]:
        """Return one agent's Docket endpoint and provision its scoped ACL user."""

        if not self.enabled:
            raise TaskBackendError("The FastMCP task backend is not enabled on this deployment.")
        if not _AGENT_ID.fullmatch(agent_id):
            raise InvalidAgentIdError(
                "agent_id must be 1-64 characters using letters, digits, '.', '_' or '-'."
            )
        self._authorize(authorization)
        descriptor = self.descriptor(request_host)
        queue_name = self.queue_name(agent_id)
        response: dict[str, Any] = {
            **descriptor,
            "agent_id": agent_id,
            "queue_name": queue_name,
        }
        if self.auth_mode == "session":
            username, password = await self._provision_acl(agent_id, queue_name)
            response["username"] = username
            response["password"] = password
        if self.settings.task_backend_tls:
            response["ca_pem"] = self._read_ca_pem()
        return response

    def queue_name(self, agent_id: str) -> str:
        """Return a stable queue namespace unique to one deployment and agent."""

        agent_hash = hashlib.sha256(agent_id.encode()).hexdigest()[:16]
        return f"{self._queue_prefix()}-{agent_hash}"

    def _queue_prefix(self) -> str:
        deployment = re.sub(r"[^A-Za-z0-9_.-]", "-", self.settings.deployment_id)
        return f"clio-web-{deployment}"

    def _authorize(self, authorization: str | None) -> None:
        token = self.settings.task_backend_api_token
        if token is None:
            return
        supplied = ""
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not secrets.compare_digest(supplied, token.get_secret_value()):
            raise TaskBackendAuthorizationError(
                "Task-backend discovery requires a valid CLIO Web Search bearer token."
            )

    async def _provision_acl(self, agent_id: str, queue_name: str) -> tuple[str, str]:
        secret = self.settings.task_backend_credential_secret
        if secret is None:
            raise TaskBackendError(
                "Secure discovery is enabled but TASK_BACKEND_CREDENTIAL_SECRET is missing."
            )
        digest = hmac.new(
            secret.get_secret_value().encode(), agent_id.encode(), hashlib.sha256
        ).digest()
        username = f"clio-{hashlib.sha256(agent_id.encode()).hexdigest()[:16]}"
        password = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        client = self._client()
        try:
            await client.execute_command(  # type: ignore[no-untyped-call]
                "ACL",
                "SETUSER",
                username,
                "reset",
                "on",
                f">{password}",
                f"~{queue_name}:*",
                f"&{queue_name}:*",
                "+@all",
                "-@dangerous",
            )
        except Exception as exc:
            raise TaskBackendError(
                "Valkey could not provision the agent task queue; check its ACL administrator "
                "credentials and retry."
            ) from exc
        finally:
            await client.aclose()
        return username, password

    def _read_ca_pem(self) -> str:
        path = self.settings.task_backend_ca_path
        if path is None:
            raise TaskBackendError(
                "TLS task discovery is enabled but TASK_BACKEND_CA_PATH is missing."
            )
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise TaskBackendError(
                "The configured Valkey CA certificate cannot be read; repair the mounted "
                "certificate and retry."
            ) from exc

    def _client(self) -> Redis:
        configured = self.settings.task_backend_url
        if configured is None:
            raise TaskBackendError("The FastMCP task backend is not configured.")
        return cast(  # pyright: ignore[reportUnnecessaryCast]
            Redis,
            Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
                configured.get_secret_value(), decode_responses=True
            ),
        )
