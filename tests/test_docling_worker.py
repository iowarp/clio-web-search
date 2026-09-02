"""Converter-construction tests for the Docling worker process."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from docling.datamodel.base_models import InputFormat
from docling.datamodel.settings import settings as docling_settings

from clio_web_search.docling_worker import _build_converter


@pytest.fixture(autouse=True)
def _restore_inference_settings() -> Iterator[None]:
    """Keep Docling's process-global inference policy unchanged for other tests."""

    original = docling_settings.inference.compile_torch_models
    try:
        yield
    finally:
        docling_settings.inference.compile_torch_models = original


@pytest.mark.parametrize("compile_torch_models", [False, True])
def test_build_converter_applies_and_logs_the_compilation_policy(
    caplog: pytest.LogCaptureFixture,
    compile_torch_models: bool,
) -> None:
    """The effective torch-compilation policy is applied and visible in the log."""

    with caplog.at_level(logging.INFO, logger="clio_web_search.docling_worker"):
        converter = _build_converter(None, compile_torch_models=compile_torch_models)

    assert docling_settings.inference.compile_torch_models is compile_torch_models
    assert converter.format_to_options[InputFormat.PDF].pipeline_options is not None
    messages = [record.getMessage() for record in caplog.records]
    assert any(str(compile_torch_models).lower() in message.lower() for message in messages), (
        messages
    )


def test_build_converter_binds_the_configured_artifacts_path(tmp_path: Path) -> None:
    """A deployment-provided artifacts path reaches the PDF pipeline options."""

    converter = _build_converter(str(tmp_path), compile_torch_models=False)

    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert options is not None
    assert options.artifacts_path == tmp_path


def test_build_converter_defaults_to_no_artifacts_path() -> None:
    """An unset artifacts path leaves Docling on its own model resolution."""

    converter = _build_converter(None, compile_torch_models=False)

    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert options is not None
    assert options.artifacts_path is None
