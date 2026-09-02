"""Named, versioned derivative manifests for converted documents."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast


def build_derivative_manifest(
    *,
    filename: str,
    markdown: str,
    html: str,
    structure: dict[str, Any],
) -> dict[str, Any]:
    """Describe named, versioned views without duplicating Docling structure.

    Textual renderings are carried inline for the custody service to persist.
    Structured nodes remain canonical in ``document.structure`` and are named
    by JSON selector so consumers can retrieve them through bounded tools.
    """

    stem = Path(filename).stem or "document"
    entries: list[dict[str, Any]] = [
        {
            "id": "markdown",
            "name": f"{stem}.md",
            "kind": "markdown",
            "media_type": "text/markdown",
            "content": markdown,
        },
        {
            "id": "html",
            "name": f"{stem}.html",
            "kind": "html",
            "media_type": "text/html",
            "content": html,
        },
        {
            "id": "preview",
            "name": f"{stem}.preview.html",
            "kind": "preview",
            "media_type": "text/html",
            "source": "html",
        },
    ]
    for collection, singular in (("pages", "page"), ("tables", "table"), ("pictures", "figure")):
        value = structure.get(collection)
        if isinstance(value, dict):
            keys = list(cast(dict[str, Any], value))
            for index, key in enumerate(keys):
                entries.append(
                    {
                        "id": f"{singular}-{index + 1}",
                        "name": f"{stem}.{singular}-{index + 1}.json",
                        "kind": singular,
                        "media_type": "application/json",
                        "selector": f"$.{collection}.{key}",
                    }
                )
        elif isinstance(value, list):
            items = cast(list[object], value)
            for index in range(len(items)):
                entries.append(
                    {
                        "id": f"{singular}-{index + 1}",
                        "name": f"{stem}.{singular}-{index + 1}.json",
                        "kind": singular,
                        "media_type": "application/json",
                        "selector": f"$.{collection}[{index}]",
                    }
                )
    texts = structure.get("texts")
    text_items = cast(list[object], texts) if isinstance(texts, list) else []
    if any(
        isinstance(item, dict) and cast(dict[str, Any], item).get("prov") for item in text_items
    ):
        entries.append(
            {
                "id": "ocr-evidence",
                "name": f"{stem}.ocr-evidence.json",
                "kind": "ocr_evidence",
                "media_type": "application/json",
                "selector": "$.texts[?(@.prov)]",
            }
        )
    return {"schema": "clio.resource-derivatives.v1", "entries": entries}
