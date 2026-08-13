"""DOI metadata and lawful open-copy resolution."""

from __future__ import annotations

import re
from typing import cast
from urllib.parse import quote

import httpx

from clio_search.config import Settings

_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def normalize_doi(value: str) -> str:
    """Normalize a bare DOI, ``doi:`` value, or doi.org URL."""

    candidate = value.strip()
    lowered = candidate.lower()
    if lowered.startswith(("https://doi.org/", "http://doi.org/")):
        candidate = candidate.split("doi.org/", 1)[1]
    elif lowered.startswith("doi:"):
        candidate = candidate[4:]
    candidate = candidate.strip()
    if not _DOI_RE.fullmatch(candidate):
        raise ValueError("target is not a valid DOI")
    return candidate


def is_doi(value: str) -> bool:
    """Return whether a target has a supported DOI form."""

    try:
        normalize_doi(value)
    except ValueError:
        return False
    return True


def _people(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    people: list[str] = []
    for raw_person in cast(list[object], values):
        if isinstance(raw_person, dict):
            person = cast(dict[str, object], raw_person)
            name = " ".join(
                part.strip()
                for part in (str(person.get("given", "")), str(person.get("family", "")))
                if part.strip()
            )
            if not name:
                name = str(person.get("name", "")).strip()
            if name:
                people.append(name)
        elif isinstance(raw_person, str) and raw_person.strip():
            people.append(raw_person.strip())
    return people


def _crossref_metadata(message: dict[str, object]) -> dict[str, object]:
    title_value = message.get("title")
    titles = cast(list[object], title_value) if isinstance(title_value, list) else []
    title_item: object | None = titles[0] if titles else None
    title = str(title_item) if title_item is not None else None
    if title is None and title_value is not None and not isinstance(title_value, list):
        title = str(title_value)
    published_value = message.get("published-online") or message.get("published-print")
    published = (
        cast(dict[str, object], published_value) if isinstance(published_value, dict) else {}
    )
    date_parts_value = published.get("date-parts")
    date_parts = cast(list[object], date_parts_value) if isinstance(date_parts_value, list) else []
    date = None
    if date_parts and isinstance(date_parts[0], list):
        date = "-".join(str(part) for part in cast(list[object], date_parts[0]))
    container = message.get("container-title")
    containers = cast(list[object], container) if isinstance(container, list) else []
    journal_item: object | None = containers[0] if containers else None
    journal = str(journal_item) if journal_item is not None else None
    if journal is None and container is not None and not isinstance(container, list):
        journal = str(container)
    return {
        "title": title,
        "authors": _people(message.get("author")),
        "published_at": date,
        "journal": journal,
        "publisher": message.get("publisher"),
        "document_type": message.get("type"),
        "license": message.get("license"),
    }


def _datacite_metadata(attributes: dict[str, object]) -> dict[str, object]:
    titles = attributes.get("titles")
    title = None
    if isinstance(titles, list) and titles and isinstance(titles[0], dict):
        title = cast(dict[str, object], titles[0]).get("title")
    publisher = attributes.get("publisher")
    if isinstance(publisher, dict):
        publisher = cast(dict[str, object], publisher).get("name")
    types_value = attributes.get("types")
    types = cast(dict[str, object], types_value) if isinstance(types_value, dict) else {}
    return {
        "title": title,
        "authors": _people(attributes.get("creators")),
        "published_at": attributes.get("published") or attributes.get("publicationYear"),
        "publisher": publisher,
        "document_type": types.get("resourceTypeGeneral"),
        "url": attributes.get("url"),
    }


async def resolve_doi(doi: str, settings: Settings) -> dict[str, object]:
    """Resolve DOI metadata and ordered lawful access candidates."""

    normalized = normalize_doi(doi)
    encoded = quote(normalized, safe="")
    warnings: list[dict[str, str]] = []
    metadata: dict[str, object] = {"doi": normalized}
    sources_queried: list[str] = []
    candidates: list[dict[str, object]] = []
    headers = {"User-Agent": "clio-search/0.1.0"}
    if settings.contact_email:
        headers["User-Agent"] += f" (mailto:{settings.contact_email})"

    timeout = httpx.Timeout(settings.request_timeout_s)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        crossref_url = f"https://api.crossref.org/works/{encoded}"
        if settings.contact_email:
            crossref_url += f"?mailto={quote(settings.contact_email, safe='')}"
        try:
            sources_queried.append("crossref")
            response = await client.get(crossref_url)
            if response.status_code == 200:
                body = cast(dict[str, object], response.json())
                message = body.get("message", {})
                if isinstance(message, dict):
                    typed_message = cast(dict[str, object], message)
                    metadata.update(
                        {k: v for k, v in _crossref_metadata(typed_message).items() if v}
                    )
            elif response.status_code == 404:
                warnings.append({"code": "crossref_not_found", "source": "crossref"})
            else:
                warnings.append({"code": "crossref_unavailable", "source": "crossref"})
        except (httpx.HTTPError, ValueError):
            warnings.append({"code": "crossref_unavailable", "source": "crossref"})

        if len(metadata) == 1:
            try:
                sources_queried.append("datacite")
                response = await client.get(f"https://api.datacite.org/dois/{encoded}")
                if response.status_code == 200:
                    body = cast(dict[str, object], response.json())
                    data_value = body.get("data")
                    data = (
                        cast(dict[str, object], data_value) if isinstance(data_value, dict) else {}
                    )
                    attributes = data.get("attributes", {})
                    if isinstance(attributes, dict):
                        resolved = _datacite_metadata(cast(dict[str, object], attributes))
                        metadata.update({k: v for k, v in resolved.items() if v})
                        if resolved.get("url"):
                            candidates.append(
                                {"url": resolved["url"], "source": "datacite", "version": None}
                            )
                elif response.status_code == 404:
                    warnings.append({"code": "datacite_not_found", "source": "datacite"})
                else:
                    warnings.append({"code": "datacite_unavailable", "source": "datacite"})
            except (httpx.HTTPError, ValueError, TypeError):
                warnings.append({"code": "datacite_unavailable", "source": "datacite"})

        if settings.contact_email:
            try:
                sources_queried.append("unpaywall")
                response = await client.get(
                    f"https://api.unpaywall.org/v2/{encoded}",
                    params={"email": settings.contact_email},
                )
                if response.status_code == 200:
                    record = cast(dict[str, object], response.json())
                    best = record.get("best_oa_location")
                    if isinstance(best, dict):
                        typed_best = cast(dict[str, object], best)
                        url = typed_best.get("url_for_pdf") or typed_best.get("url")
                        if url:
                            candidates.insert(
                                0,
                                {
                                    "url": str(url),
                                    "source": "unpaywall",
                                    "version": typed_best.get("version"),
                                    "license": typed_best.get("license"),
                                    "host_type": typed_best.get("host_type"),
                                },
                            )
                elif response.status_code != 404:
                    warnings.append({"code": "unpaywall_unavailable", "source": "unpaywall"})
            except (httpx.HTTPError, ValueError):
                warnings.append({"code": "unpaywall_unavailable", "source": "unpaywall"})
        else:
            warnings.append(
                {
                    "code": "unpaywall_disabled_contact_email_missing",
                    "source": "unpaywall",
                }
            )

    candidates.append(
        {"url": f"https://doi.org/{normalized}", "source": "doi", "version": "landing_page"}
    )
    unique_candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = str(candidate.get("url", "")).strip()
        if url and url not in seen:
            seen.add(url)
            unique_candidates.append(candidate)
    return {
        "doi": normalized,
        "metadata": metadata,
        "candidates": unique_candidates,
        "sources_queried": sources_queried,
        "warnings": warnings,
    }
