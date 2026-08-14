"""Generate private SearXNG settings for one installation."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path
from typing import Any

import yaml

from clio_web_search.config import Settings

_ENGINE_NAMES = [
    "arxiv",
    "askubuntu",
    "brave",
    "crossref",
    "datacite",
    "duckduckgo",
    "github",
    "mojeek",
    "openalex",
    "pubmed",
    "semantic scholar",
    "stackoverflow",
    "startpage",
    "superuser",
]


def build_searxng_settings(settings: Settings, *, secret_key: str) -> dict[str, Any]:
    """Return settings with free engines and no shared identity or paid provider."""

    engines: list[dict[str, Any]] = [
        {
            "name": "crossref",
            "engine": "crossref",
            "shortcut": "cr",
            "categories": "science, scientific publications",
            "mailto": settings.contact_email or "",
            "disabled": False,
        },
        {
            "name": "datacite",
            "engine": "datacite",
            "shortcut": "dc",
            "categories": "science, scientific publications",
            "mailto": settings.contact_email or "",
            "disabled": False,
        },
        {
            "name": "openalex",
            "engine": "openalex",
            "shortcut": "oa",
            "categories": "science, scientific publications",
            "mailto": settings.contact_email or "",
            "api_key": settings.openalex_api_key or "",
            "disabled": False,
        },
    ]
    return {
        "use_default_settings": {"engines": {"keep_only": _ENGINE_NAMES}},
        "general": {
            "debug": False,
            "instance_name": "CLIO Web Search",
            "enable_metrics": True,
        },
        "search": {
            "safe_search": 0,
            "autocomplete": "",
            "default_lang": "en",
            "formats": ["html", "json"],
        },
        "server": {
            "port": 8888,
            "bind_address": "127.0.0.1",
            "secret_key": secret_key,
            "limiter": False,
            "public_instance": False,
            "image_proxy": False,
            "method": "GET",
        },
        "outgoing": {"request_timeout": 5.0, "max_request_timeout": 15.0},
        "engines": engines,
    }


def write_searxng_settings(settings: Settings) -> Path:
    """Persist generated settings and a stable per-installation secret."""

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    secret_path = settings.data_dir / "searxng-secret"
    if secret_path.exists():
        secret = secret_path.read_text(encoding="utf-8").strip()
    else:
        secret = secrets.token_hex(32)
        secret_path.write_text(secret, encoding="utf-8")
        secret_path.chmod(0o600)
    target = settings.data_dir / "searxng-settings.yml"
    target.write_text(
        yaml.safe_dump(build_searxng_settings(settings, secret_key=secret), sort_keys=False),
        encoding="utf-8",
    )
    target.chmod(0o600)
    return target


def main() -> None:
    """Write settings and print their path for the container entrypoint."""

    sys.stdout.write(f"{write_searxng_settings(Settings())}\n")


if __name__ == "__main__":
    main()
