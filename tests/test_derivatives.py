"""Derivative-manifest contract tests written against the consuming accessor."""

from __future__ import annotations

import re
from typing import Any

from clio_web_search.derivatives import build_derivative_manifest

# The custody service persists an entry only when its id matches this pattern and
# its ``content`` is a bare string; structured nodes are fetched positionally with
# ``node(collection, index)``.
_CONSUMER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_NODE_COLLECTIONS = {"pages", "tables", "pictures", "texts"}


def _structure(*, pages: int = 0, tables: int = 0, pictures: int = 0) -> dict[str, Any]:
    """Build a Docling-shaped structure with the requested collection sizes."""

    return {
        "pages": {str(number): {"page_no": number} for number in range(1, pages + 1)},
        "tables": [{"data": [[f"table-{index}"]]} for index in range(tables)],
        "pictures": [{"image": f"picture-{index}"} for index in range(pictures)],
        "texts": [{"text": "body", "prov": [{"page_no": 1}]}],
    }


def _node(structure: dict[str, Any], collection: str, index: int) -> Any:
    """Resolve one entry the way the consuming ``node(collection, index)`` accessor does."""

    values = structure[collection]
    if isinstance(values, dict):
        keys = list(values)
        return values[keys[index]]
    return values[index]


def test_every_entry_is_resolvable_by_the_consumer() -> None:
    """Each entry carries inline content or an explicit collection and index."""

    structure = _structure(pages=2, tables=1, pictures=1)
    manifest = build_derivative_manifest(
        filename="paper.pdf",
        markdown="# Paper",
        html="<p>Paper</p>",
        structure=structure,
    )

    assert manifest["schema"] == "clio.resource-derivatives.v1"
    for entry in manifest["entries"]:
        assert _CONSUMER_ID.fullmatch(str(entry["id"])), entry
        assert "selector" not in entry, entry
        assert "source" not in entry, entry
        if "content" in entry:
            assert isinstance(entry["content"], str), entry
            continue
        assert entry["collection"] in _NODE_COLLECTIONS, entry
        assert isinstance(entry["index"], int), entry
        assert _node(structure, str(entry["collection"]), int(entry["index"])) is not None


def test_page_entries_use_positional_indexes_into_a_dict_collection() -> None:
    """Docling keys pages by page number; the consumer indexes them positionally."""

    structure = _structure(pages=3)
    manifest = build_derivative_manifest(
        filename="paper.pdf",
        markdown="# Paper",
        html="<p>Paper</p>",
        structure=structure,
    )

    pages = [entry for entry in manifest["entries"] if entry["kind"] == "page"]
    assert [(entry["id"], entry["collection"], entry["index"]) for entry in pages] == [
        ("page-1", "pages", 0),
        ("page-2", "pages", 1),
        ("page-3", "pages", 2),
    ]
    assert _node(structure, "pages", 0) == {"page_no": 1}


def test_filter_expression_entry_is_not_advertised() -> None:
    """No entry may name a filter expression the consumer cannot evaluate."""

    manifest = build_derivative_manifest(
        filename="paper.pdf",
        markdown="# Paper",
        html="<p>Paper</p>",
        structure=_structure(pages=1),
    )

    assert [entry for entry in manifest["entries"] if entry["id"] == "ocr-evidence"] == []


def test_empty_html_is_not_advertised_as_a_derivative() -> None:
    """A converter that produced no HTML must not present an empty success."""

    manifest = build_derivative_manifest(
        filename="paper.pdf",
        markdown="# Paper",
        html="",
        structure=_structure(tables=1),
    )

    assert [entry["id"] for entry in manifest["entries"]] == ["markdown", "table-1"]


def test_entry_count_is_capped_and_truncation_is_reported() -> None:
    """A long document keeps whole-document and structured views within the cap."""

    manifest = build_derivative_manifest(
        filename="paper.pdf",
        markdown="# Paper",
        html="<p>Paper</p>",
        structure=_structure(pages=40, tables=6, pictures=4),
        max_entries=14,
    )
    identifiers = [str(entry["id"]) for entry in manifest["entries"]]

    assert len(identifiers) == 14
    assert identifiers[:2] == ["markdown", "html"]
    assert [value for value in identifiers if value.startswith("table-")] == [
        f"table-{number}" for number in range(1, 7)
    ]
    assert [value for value in identifiers if value.startswith("figure-")] == [
        f"figure-{number}" for number in range(1, 5)
    ]
    assert [value for value in identifiers if value.startswith("page-")] == [
        "page-1",
        "page-2",
    ]
    assert manifest["entries_truncated"] is True
    assert manifest["entry_counts"]["included"] == 14
    assert manifest["entry_counts"]["available"] == 52
    assert manifest["entry_counts"]["omitted"] == 38
    assert manifest["entry_counts"]["by_kind"]["page"] == {"available": 40, "included": 2}
    assert manifest["entry_counts"]["by_kind"]["table"] == {"available": 6, "included": 6}


def test_default_cap_bounds_a_long_paper() -> None:
    """The default cap keeps a forty-page paper's manifest bounded."""

    manifest = build_derivative_manifest(
        filename="paper.pdf",
        markdown="# Paper",
        html="<p>Paper</p>",
        structure=_structure(pages=40, tables=20, pictures=14),
    )

    assert len(manifest["entries"]) == 32
    assert manifest["entries_truncated"] is True


def test_untruncated_manifest_reports_complete_counts() -> None:
    """A short document reports no truncation and matching counts."""

    manifest = build_derivative_manifest(
        filename="paper.pdf",
        markdown="# Paper",
        html="<p>Paper</p>",
        structure=_structure(pages=2, tables=1),
    )

    assert manifest["entries_truncated"] is False
    assert manifest["entry_counts"]["included"] == manifest["entry_counts"]["available"] == 5
    assert manifest["entry_counts"]["omitted"] == 0


def test_whole_document_entries_survive_a_tiny_cap() -> None:
    """Markdown and HTML are never dropped to satisfy the entry budget."""

    manifest = build_derivative_manifest(
        filename="paper.pdf",
        markdown="# Paper",
        html="<p>Paper</p>",
        structure=_structure(pages=5, tables=2),
        max_entries=1,
    )

    assert [str(entry["id"]) for entry in manifest["entries"]] == ["markdown", "html"]
    assert manifest["entries_truncated"] is True
