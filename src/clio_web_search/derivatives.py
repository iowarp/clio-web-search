"""Named, versioned derivative manifests for converted documents."""

from __future__ import annotations

from collections.abc import Sized
from pathlib import Path
from typing import Any, cast

DEFAULT_MAX_DERIVATIVE_ENTRIES = 32
_NODE_COLLECTIONS: tuple[tuple[str, str], ...] = (
    ("tables", "table"),
    ("pictures", "figure"),
    ("pages", "page"),
)


def _node_entries(
    stem: str,
    collection: str,
    singular: str,
    value: object,
) -> list[dict[str, Any]]:
    """Name every positional node in one Docling collection.

    Docling keys ``pages`` by page number and stores ``tables`` and ``pictures``
    as lists. Consumers read a node with a positional ``(collection, index)``
    accessor, so both shapes are named by their position, never by their key.
    """

    if not isinstance(value, dict | list):
        return []
    count = len(cast(Sized, value))
    return [
        {
            "id": f"{singular}-{index + 1}",
            "name": f"{stem}.{singular}-{index + 1}.json",
            "kind": singular,
            "media_type": "application/json",
            "collection": collection,
            "index": index,
        }
        for index in range(count)
    ]


def _entry_counts(
    available: list[dict[str, Any]], selected: list[dict[str, Any]]
) -> dict[str, Any]:
    """Summarize which named views exist and which ones this manifest lists."""

    by_kind: dict[str, dict[str, int]] = {}
    for entry in available:
        by_kind.setdefault(str(entry["kind"]), {"available": 0, "included": 0})["available"] += 1
    for entry in selected:
        by_kind[str(entry["kind"])]["included"] += 1
    return {
        "included": len(selected),
        "available": len(available),
        "omitted": len(available) - len(selected),
        "by_kind": by_kind,
    }


def build_derivative_manifest(
    *,
    filename: str,
    markdown: str,
    html: str,
    structure: dict[str, Any],
    max_entries: int = DEFAULT_MAX_DERIVATIVE_ENTRIES,
) -> dict[str, Any]:
    """Describe named, versioned views without duplicating Docling structure.

    Textual renderings are carried inline as bare ``content`` strings for the
    custody service to persist. Structured nodes remain canonical in
    ``document.structure`` and are named by ``collection`` plus a positional
    ``index`` so consumers retrieve them through their bounded node accessor.

    Whole-document renderings are always listed. Structured nodes are listed
    until ``max_entries`` is reached, preferring tables and figures over
    per-page nodes, because the manifest is replayed into a consuming agent's
    prompt on every turn. ``entries_truncated`` and ``entry_counts`` report what
    the cap left out.
    """

    stem = Path(filename).stem or "document"
    whole_document: list[dict[str, Any]] = [
        {
            "id": "markdown",
            "name": f"{stem}.md",
            "kind": "markdown",
            "media_type": "text/markdown",
            "content": markdown,
        }
    ]
    if html:
        whole_document.append(
            {
                "id": "html",
                "name": f"{stem}.html",
                "kind": "html",
                "media_type": "text/html",
                "content": html,
            }
        )
    groups = [
        _node_entries(stem, collection, singular, structure.get(collection))
        for collection, singular in _NODE_COLLECTIONS
    ]
    selected = list(whole_document)
    remaining = max(max_entries - len(selected), 0)
    for group in groups:
        admitted = group[:remaining]
        selected.extend(admitted)
        remaining -= len(admitted)
    available = whole_document + [entry for group in groups for entry in group]
    return {
        "schema": "clio.resource-derivatives.v1",
        "entries": selected,
        "entries_truncated": len(selected) < len(available),
        "entry_counts": _entry_counts(available, selected),
    }
