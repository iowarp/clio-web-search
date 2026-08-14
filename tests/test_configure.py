"""Per-installation SearXNG configuration tests."""

from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import cast

import pytest

from clio_web_search.config import Settings
from clio_web_search.configure import build_searxng_settings, write_searxng_settings


def test_settings_use_web_search_environment_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLIO_WEB_SEARCH_WORKERS", "3")

    assert Settings().workers == 3


def test_openalex_patch_suppresses_unverified_abstract(tmp_path: Path) -> None:
    """OpenAlex bibliographic rows must not carry known cross-work abstract leakage."""

    engine = tmp_path / "openalex.py"
    engine.write_text(
        'mailto = ""\n'
        '    if isinstance(mailto, str) and mailto != "":\n'
        '        args["mailto"] = mailto\n'
        '        content: str = _reconstruct_abstract(item.get("abstract_inverted_index")) or ""\n',
        encoding="utf-8",
    )

    patch_namespace = run_path(str(Path(__file__).parents[1] / "container" / "patch_searxng.py"))
    patch_openalex = cast(Callable[[Path], None], patch_namespace["patch_openalex"])
    patch_openalex(engine)

    patched = engine.read_text(encoding="utf-8")
    assert 'api_key = ""' in patched
    assert 'content: str = ""' in patched
    assert "_reconstruct_abstract(item.get" not in patched


def test_config_has_free_engines_and_no_shared_identity(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    generated = build_searxng_settings(settings, secret_key=tmp_path.name)

    assert "datacite" in generated["use_default_settings"]["engines"]["keep_only"]
    engines = {engine["name"]: engine for engine in generated["engines"]}
    assert engines["crossref"]["mailto"] == ""
    assert engines["crossref"]["disabled"] is False
    assert engines["datacite"]["mailto"] == ""
    assert engines["openalex"]["api_key"] == ""
    assert not any("tavily" in str(engine).lower() for engine in generated["engines"])


def test_deployer_identity_is_written_and_secret_is_stable(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        contact_email="owner@example.org",
        openalex_api_key="free-key",
    )
    first = write_searxng_settings(settings)
    secret = (tmp_path / "searxng-secret").read_text(encoding="utf-8")
    second = write_searxng_settings(settings)

    assert first == second
    assert (tmp_path / "searxng-secret").read_text(encoding="utf-8") == secret
    contents = first.read_text(encoding="utf-8")
    assert "owner@example.org" in contents
    assert "free-key" in contents
