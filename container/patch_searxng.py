"""Apply pinned SearXNG scholarly-engine enrichments."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path


def _replace_once(path: Path, before: str, after: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(before) != 1:
        raise RuntimeError(f"pinned SearXNG patch anchor changed in {path}")
    path.write_text(text.replace(before, after), encoding="utf-8")


def patch_openalex(path: Path) -> None:
    """Add key support and suppress unverified cross-work abstract text."""

    _replace_once(path, 'mailto = ""\n', 'mailto = ""\napi_key = ""\n')
    _replace_once(
        path,
        '    if isinstance(mailto, str) and mailto != "":\n        args["mailto"] = mailto\n',
        '    if isinstance(mailto, str) and mailto != "":\n'
        '        args["mailto"] = mailto\n'
        '    if isinstance(api_key, str) and api_key != "":\n'
        '        args["api_key"] = api_key\n',
    )
    _replace_once(
        path,
        '        content: str = _reconstruct_abstract(item.get("abstract_inverted_index")) or ""\n',
        "        # OpenAlex can attach an abstract from a different work to otherwise matching\n"
        "        # identifiers. Preserve the bibliographic fields, but do not present that text\n"
        "        # as a trustworthy snippet. Agents can fetch the canonical paper instead.\n"
        '        content: str = ""\n',
    )


def main() -> None:
    """Install DataCite and contact/key support into pinned engines."""

    spec = importlib.util.find_spec("searx")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("installed SearXNG package not found")
    engine_dir = Path(next(iter(spec.submodule_search_locations))) / "engines"
    shutil.copy2(Path(__file__).with_name("datacite.py"), engine_dir / "datacite.py")

    crossref = engine_dir / "crossref.py"
    _replace_once(
        crossref,
        'search_url = "https://api.crossref.org/works"\n',
        'search_url = "https://api.crossref.org/works"\nmailto = ""\n',
    )
    _replace_once(
        crossref,
        '    params["url"] = f"{search_url}?{urlencode(args)}"\n',
        "    if mailto:\n"
        '        args["mailto"] = mailto\n'
        '        params["headers"]["User-Agent"] = '
        'f"SearXNG Crossref engine (mailto:{mailto})"\n'
        '    params["url"] = f"{search_url}?{urlencode(args)}"\n',
    )

    patch_openalex(engine_dir / "openalex.py")


if __name__ == "__main__":
    main()
