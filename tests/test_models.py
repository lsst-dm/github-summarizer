"""Tests for the data models."""

from datetime import UTC, datetime

from lsst.github_summarizer.models import (
    ActivityStatus,
    Repository,
    RepositorySummary,
)


def test_repository_defaults() -> None:
    repo = Repository(name="example", url="https://github.com/lsst/example")
    assert repo.description is None
    assert repo.topics == []
    assert repo.is_archived is False
    assert repo.pushed_at is None
    assert repo.default_branch_commit_count is None
    assert repo.recent_commit_dates == []


def test_repository_parses_pushed_at_from_iso_string() -> None:
    repo = Repository(
        name="example",
        url="https://example",
        pushed_at="2026-01-02T03:04:05Z",
    )
    assert repo.pushed_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_repository_parses_recent_commit_dates_from_iso_strings() -> None:
    repo = Repository(
        name="example",
        url="https://example",
        recent_commit_dates=["2026-01-02T03:04:05Z"],
    )
    assert repo.recent_commit_dates == [datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)]


def test_repository_summary_requires_derived_fields() -> None:
    repo = Repository(name="example", url="https://example")
    summary = RepositorySummary(
        repo=repo,
        activity=ActivityStatus.ACTIVE,
        group="Pipelines",
        grouping_reason="topic:pipelines",
    )
    assert summary.repo.name == "example"
    assert summary.activity is ActivityStatus.ACTIVE
    assert summary.group == "Pipelines"
