"""OpenAlex source contract tests for resolve_doi()."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from clio_web_search.config import Settings
from clio_web_search.doi import resolve_doi

_OPENALEX_BODY_WITH_OA_URL: dict[str, Any] = {
    "display_name": "Example Paper",
    "authorships": [{"author": {"display_name": "Jane Doe"}}],
    "publication_date": "2021-05-24",
    "primary_location": {"source": {"display_name": "Example Journal"}},
    "open_access": {"oa_url": "https://example.org/open-copy.pdf"},
}

_OPENALEX_BODY_WITHOUT_OA_URL: dict[str, Any] = {
    "display_name": "Example Paper",
    "authorships": [{"author": {"display_name": "Jane Doe"}}],
    "publication_date": "2021-05-24",
    "primary_location": {"source": {"display_name": "Example Journal"}},
    "open_access": {"oa_url": None},
}


def _response(status_code: int, json_body: dict[str, Any]) -> AsyncMock:
    """Build a fake httpx.Response returning the given status and JSON body."""
    response = AsyncMock()
    response.status_code = status_code
    response.json = lambda: json_body
    return response


def _settings(
    *, contact_email: str | None = None, openalex_api_key: str | None = None
) -> Settings:
    return Settings(contact_email=contact_email, openalex_api_key=openalex_api_key)


def _not_found(url: str) -> AsyncMock:
    """Default fake GET: every source 404s unless the caller overrides it."""
    return _response(404, {})


async def test_resolve_doi_openalex_found_adds_open_access_candidate() -> None:
    """When OpenAlex reports an oa_url, resolve_doi surfaces it as a candidate."""

    def fake_get(url: str, *args: Any, **kwargs: Any) -> AsyncMock:
        if "openalex" in str(url):
            return _response(200, _OPENALEX_BODY_WITH_OA_URL)
        return _not_found(str(url))

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await resolve_doi("10.1234/example", _settings())

    assert "openalex" in result["sources_queried"]
    assert result["metadata"]["title"] == "Example Paper"
    assert result["metadata"]["authors"] == ["Jane Doe"]
    openalex_candidates = [c for c in result["candidates"] if c["source"] == "openalex"]
    assert openalex_candidates == [
        {"url": "https://example.org/open-copy.pdf", "source": "openalex", "version": None}
    ]


async def test_resolve_doi_openalex_without_oa_url_adds_no_candidate() -> None:
    """A 200 response with no open_access.oa_url must not fabricate a candidate."""

    def fake_get(url: str, *args: Any, **kwargs: Any) -> AsyncMock:
        if "openalex" in str(url):
            return _response(200, _OPENALEX_BODY_WITHOUT_OA_URL)
        return _not_found(str(url))

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await resolve_doi("10.1234/example", _settings())

    assert not [c for c in result["candidates"] if c["source"] == "openalex"]
    assert result["metadata"]["title"] == "Example Paper"


@pytest.mark.parametrize(
    ("status_code", "raise_error", "expected_warning_code"),
    [
        (404, None, "openalex_not_found"),
        (500, None, "openalex_unavailable"),
        (None, httpx.ConnectTimeout("timed out"), "openalex_unavailable"),
    ],
)
async def test_resolve_doi_openalex_failure_modes_warn_without_crashing(
    status_code: int | None,
    raise_error: Exception | None,
    expected_warning_code: str,
) -> None:
    """OpenAlex being down, 404, or erroring degrades to a warning, never a crash."""

    def fake_get(url: str, *args: Any, **kwargs: Any) -> AsyncMock:
        if "openalex" in str(url):
            if raise_error is not None:
                raise raise_error
            return _response(status_code, {})
        return _not_found(str(url))

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await resolve_doi("10.1234/example", _settings())

    assert "openalex" in result["sources_queried"]
    assert any(
        w["code"] == expected_warning_code and w["source"] == "openalex"
        for w in result["warnings"]
    )
    assert not [c for c in result["candidates"] if c["source"] == "openalex"]


async def test_resolve_doi_omits_api_key_param_when_not_configured() -> None:
    """No OpenAlex API key configured -> the request URL carries no api_key param."""

    seen_urls: list[str] = []

    def fake_get(url: str, *args: Any, **kwargs: Any) -> AsyncMock:
        seen_urls.append(str(url))
        if "openalex" in str(url):
            return _response(200, _OPENALEX_BODY_WITH_OA_URL)
        return _not_found(str(url))

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await resolve_doi("10.1234/example", _settings(openalex_api_key=None))

    openalex_url = next(u for u in seen_urls if "openalex" in u)
    assert "api_key" not in openalex_url


async def test_resolve_doi_appends_api_key_param_when_configured() -> None:
    """A configured OpenAlex API key is appended to the request URL."""

    seen_urls: list[str] = []

    def fake_get(url: str, *args: Any, **kwargs: Any) -> AsyncMock:
        seen_urls.append(str(url))
        if "openalex" in str(url):
            return _response(200, _OPENALEX_BODY_WITH_OA_URL)
        return _not_found(str(url))

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        await resolve_doi("10.1234/example", _settings(openalex_api_key="secret-key"))

    openalex_url = next(u for u in seen_urls if "openalex" in u)
    assert "api_key=secret-key" in openalex_url


async def test_resolve_doi_queries_openalex_even_when_crossref_already_found_metadata() -> None:
    """OpenAlex is queried unconditionally -- a Crossref hit must not skip it."""

    crossref_body = {"message": {"title": ["Crossref Title"], "author": []}}

    def fake_get(url: str, *args: Any, **kwargs: Any) -> AsyncMock:
        if "crossref" in str(url):
            return _response(200, crossref_body)
        if "openalex" in str(url):
            return _response(200, _OPENALEX_BODY_WITH_OA_URL)
        return _not_found(str(url))

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        result = await resolve_doi("10.1234/example", _settings())

    assert result["sources_queried"] == ["crossref", "openalex"]
    assert any(c["source"] == "openalex" for c in result["candidates"])