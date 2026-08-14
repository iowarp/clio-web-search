"""DOI normalization contract tests."""

import pytest

from clio_web_search.doi import is_doi, normalize_doi


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
