"""Smoke test that the package imports."""

import lsst.github_summarizer


def test_package_imports() -> None:
    assert lsst.github_summarizer is not None
