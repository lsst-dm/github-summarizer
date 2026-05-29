"""Tests for the raw repository cache."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lsst.github_summarizer.cache import (
    CacheError,
    RawFileSource,
    RawRepositories,
    load_raw,
    save_raw,
)
from lsst.github_summarizer.models import Repository

FETCHED = datetime(2026, 6, 1, tzinfo=UTC)


def _repos() -> list[Repository]:
    return [
        Repository(
            name="afw",
            url="https://github.com/lsst/afw",
            topics=["pipelines"],
            pushed_at=datetime(2026, 1, 1, tzinfo=UTC),
            is_archived=True,
        ),
        Repository(name="dmtn-001", url="https://github.com/lsst/dmtn-001"),
    ]


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "raw.json"
    save_raw(path, _repos(), org="lsst", fetched_at=FETCHED)
    data = load_raw(path)
    assert data.org == "lsst"
    assert data.fetched_at == FETCHED
    assert [r.name for r in data.repositories] == ["afw", "dmtn-001"]
    assert data.repositories[0].topics == ["pipelines"]
    assert data.repositories[0].pushed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert data.repositories[0].is_archived is True


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(CacheError):
        load_raw(tmp_path / "nope.json")


def test_load_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "raw.json"
    path.write_text("{not json")
    with pytest.raises(CacheError):
        load_raw(path)


def test_load_unknown_schema_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "raw.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 999,
                "org": "lsst",
                "fetched_at": FETCHED.isoformat(),
                "repositories": [],
            }
        )
    )
    with pytest.raises(CacheError):
        load_raw(path)


def test_raw_file_source_returns_repos() -> None:
    data = RawRepositories(org="lsst", fetched_at=FETCHED, repositories=_repos())
    source = RawFileSource(data)
    assert [r.name for r in source.fetch_repositories("lsst")] == ["afw", "dmtn-001"]


def test_raw_file_source_warns_on_org_mismatch(caplog: pytest.LogCaptureFixture) -> None:
    data = RawRepositories(org="lsst", fetched_at=FETCHED, repositories=_repos())
    source = RawFileSource(data)
    with caplog.at_level(logging.WARNING, logger="lsst.github_summarizer.cache"):
        result = source.fetch_repositories("other")
    assert [r.name for r in result] == ["afw", "dmtn-001"]
    assert any("other" in record.getMessage() for record in caplog.records)
