"""DOI normalization and resolution contract tests."""

from unittest.mock import AsyncMock, patch

import pytest

from clio_web_search.config import Settings
from clio_web_search.doi import is_doi, normalize_doi, resolve_doi


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.1234/example", "10.1234/example"),
        ("doi:10.1234/example", "10.1234/example"),
        ("https://doi.org/10.1234/example", "10.1234/example"),
    ],
)
def test_normalize_doi_forms(value: str, expected: str) -> None:
    assert normalize_doi(value) == expected
    assert is_doi(value)


def test_invalid_doi_is_rejected() -> None:
    with pytest.raises(ValueError, match="valid DOI"):
        normalize_doi("not-a-doi")
    assert not is_doi("not-a-doi")


def _fake_response(status_code: int, json_body: dict) -> AsyncMock:
    response = AsyncMock()
    response.status_code = status_code
    response.json = lambda: json_body
    return response


def _fake_get(url, *args, **kwargs):
    text = str(url)
    if "crossref" in text:
        return _fake_response(404, {})
    if "datacite" in text:
        return _fake_response(404, {})
    if "openalex" in text:
        return _fake_response(
            200,
            {
                "display_name": "Example Paper",
                "authorships": [{"author": {"display_name": "Jane Doe"}}],
                "publication_date": "2021-05-24",
                "primary_location": {"source": {"display_name": "Example Journal"}},
                "open_access": {"oa_url": "https://example.org/open-copy.pdf"},
            },
        )
    return _fake_response(404, {})


async def test_resolve_doi_queries_openalex_and_adds_candidate() -> None:
    settings = Settings(contact_email=None, openalex_api_key=None)
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=_fake_get)):
        result = await resolve_doi("10.1234/example", settings)

    assert "openalex" in result["sources_queried"]
    assert result["metadata"]["title"] == "Example Paper"
    assert result["metadata"]["authors"] == ["Jane Doe"]
    assert any(candidate["source"] == "openalex" for candidate in result["candidates"])
