"""GROBID TEI enrichment for scholarly PDFs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
from lxml import etree

from clio_search.config import Settings

_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


def _text(element: Any | None) -> str | None:
    if element is None:
        return None
    raw = etree.tostring(element, method="text", encoding="unicode", with_tail=False)
    value = " ".join(raw.split())
    return value or None


def _person_name(element: Any | None) -> str | None:
    """Normalize a TEI person name without joining inline name parts."""

    if element is None:
        return None
    parts = [
        value
        for value in (_text(child) for child in element.findall("./tei:forename", _NS))
        if value
    ]
    surname = _text(element.find("./tei:surname", _NS))
    if surname:
        parts.append(surname)
    return " ".join(parts) if parts else _text(element)


def _parse_tei(tei: str) -> dict[str, Any]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(tei.encode(), parser=parser)
    title = _text(root.find(".//tei:titleStmt/tei:title", _NS))
    authors = [
        value
        for value in (_text(node) for node in root.findall(".//tei:titleStmt/tei:author", _NS))
        if value
    ]
    if not authors:
        authors = [
            value
            for value in (
                _person_name(node)
                for node in root.findall(
                    ".//tei:teiHeader/tei:fileDesc/tei:sourceDesc"
                    "/tei:biblStruct/tei:analytic/tei:author/tei:persName",
                    _NS,
                )
            )
            if value
        ]
    doi = _text(root.find(".//tei:idno[@type='DOI']", _NS))
    published = root.find(".//tei:publicationStmt/tei:date[@type='published']", _NS)
    journal = _text(
        root.find(".//tei:sourceDesc/tei:biblStruct/tei:monogr/tei:title[@level='j']", _NS)
    )
    publisher = _text(root.find(".//tei:publicationStmt/tei:publisher", _NS))
    references: list[dict[str, Any]] = []
    reference_by_id: dict[str, int] = {}
    for index, item in enumerate(root.findall(".//tei:listBibl/tei:biblStruct", _NS), start=1):
        ref_id = item.get("{http://www.w3.org/XML/1998/namespace}id")
        entry = {
            "title": _text(item.find(".//tei:title[@level='a']", _NS))
            or _text(item.find(".//tei:title", _NS)),
            "authors": [
                value
                for value in (
                    _person_name(node) for node in item.findall(".//tei:author/tei:persName", _NS)
                )
                if value
            ],
            "doi": _text(item.find(".//tei:idno[@type='DOI']", _NS)),
            "url": _pointer_target(item.find(".//tei:ptr[@type='web']", _NS)),
            "raw": _text(item),
        }
        references.append({key: value for key, value in entry.items() if value})
        if ref_id:
            reference_by_id[ref_id] = index

    contexts: list[dict[str, Any]] = []
    for ref in root.findall(".//tei:ref[@type='bibr']", _NS):
        target = (ref.get("target") or "").lstrip("#")
        parent = ref.getparent()
        context = _text(parent)
        if context:
            contexts.append(
                {
                    "reference_index": reference_by_id.get(target),
                    "marker": _text(ref),
                    "text": context,
                }
            )
    return {
        "metadata": {
            key: value
            for key, value in {
                "title": title,
                "authors": authors,
                "doi": doi,
                "published_at": published.get("when") if published is not None else None,
                "journal": journal,
                "publisher": publisher,
            }.items()
            if value
        },
        "references": references,
        "citation_contexts": contexts,
    }


def _pointer_target(element: Any | None) -> str | None:
    """Return a TEI pointer's target URL when present."""

    if element is None:
        return None
    value = element.get("target")
    return str(value).strip() or None if value is not None else None


async def enrich_pdf(path: Path, settings: Settings, *, force_scholarly: bool) -> dict[str, Any]:
    """Return optional GROBID enrichment for a PDF."""

    timeout = httpx.Timeout(settings.conversion_timeout_s)
    async with httpx.AsyncClient(timeout=timeout) as client:
        with path.open("rb") as handle:
            header_response = await client.post(
                f"{settings.grobid_url.rstrip('/')}/api/processHeaderDocument",
                files={"input": (path.name, handle, "application/pdf")},
            )
        if header_response.status_code != 200:
            raise RuntimeError(f"GROBID header returned HTTP {header_response.status_code}")
        header = _parse_tei(header_response.text)
        metadata = header.get("metadata", {})
        scholarly = force_scholarly or bool(
            metadata.get("doi") or (metadata.get("title") and metadata.get("authors"))
        )
        if not scholarly:
            return {
                "profile": "general",
                "metadata": metadata,
                "references": [],
                "citation_contexts": [],
            }
        with path.open("rb") as handle:
            full_response = await client.post(
                f"{settings.grobid_url.rstrip('/')}/api/processFulltextDocument",
                files={"input": (path.name, handle, "application/pdf")},
                data={"consolidateHeader": "1", "consolidateCitations": "0"},
            )
        if full_response.status_code != 200:
            raise RuntimeError(f"GROBID full text returned HTTP {full_response.status_code}")
        parsed = _parse_tei(full_response.text)
        parsed["profile"] = "scholarly"
        return parsed


def looks_like_pdf(data: bytes, filename: str, content_type: str | None) -> bool:
    """Return whether bytes represent a PDF."""

    return (
        data.startswith(b"%PDF-")
        or filename.lower().endswith(".pdf")
        or (content_type or "").split(";", 1)[0].strip().lower() == "application/pdf"
    )


def doi_in_text(value: str | None) -> bool:
    """Return whether text contains a DOI-shaped identifier."""

    return bool(value and re.search(r"\b10\.\d{4,9}/\S+", value, re.IGNORECASE))
