# SPDX-License-Identifier: AGPL-3.0-or-later
"""SearXNG engine for the public DataCite DOI API."""

from __future__ import annotations

import typing as t
from datetime import datetime
from urllib.parse import urlencode

from searx.result_types import EngineResults

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

about = {
    "website": "https://datacite.org/",
    "official_api_documentation": "https://support.datacite.org/docs/api",
    "use_official_api": True,
    "require_api_key": False,
    "results": "JSON",
}
categories = ["science", "scientific publications"]
paging = True
search_url = "https://api.datacite.org/dois"
mailto = ""


def request(query: str, params: OnlineParams) -> None:
    """Build a respectful public DataCite API request."""

    query_params = {
        "query": query,
        "page[number]": params["pageno"],
        "page[size]": 20,
    }
    params["url"] = f"{search_url}?{urlencode(query_params)}"
    if mailto:
        params["headers"]["User-Agent"] = f"SearXNG DataCite engine (mailto:{mailto})"


def _date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    for pattern in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def _names(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item.get("name")) for item in values if isinstance(item, dict) and item.get("name")]


def response(resp: SXNG_Response) -> EngineResults:
    """Normalize DataCite records into SearXNG Paper results."""

    results = EngineResults()
    for record in resp.json().get("data", []):
        attributes = record.get("attributes", {})
        titles = attributes.get("titles", [])
        title = titles[0].get("title", "") if titles else ""
        descriptions = attributes.get("descriptions", [])
        content = descriptions[0].get("description", "") if descriptions else ""
        publisher = attributes.get("publisher", "")
        if isinstance(publisher, dict):
            publisher = publisher.get("name", "")
        types = attributes.get("types", {})
        subjects = attributes.get("subjects", [])
        tags = [item.get("subject", "") for item in subjects if item.get("subject")]
        doi = attributes.get("doi") or record.get("id", "")
        url = attributes.get("url") or f"https://doi.org/{doi}"
        results.add(
            results.types.Paper(
                title=title,
                content=content,
                url=url,
                doi=doi,
                authors=_names(attributes.get("creators")),
                publisher=publisher,
                type=types.get("resourceTypeGeneral", ""),
                tags=tags,
                publishedDate=_date(
                    attributes.get("published") or attributes.get("publicationYear")
                ),
            )
        )
    return results
