"""Tests for the orchestrator's filtering and enrichment."""

from datetime import UTC, datetime

from lsst.github_summarizer.config import Config
from lsst.github_summarizer.models import ActivityStatus, Repository
from lsst.github_summarizer.orchestrator import build_summaries

NOW = datetime(2026, 6, 1, tzinfo=UTC)


class FakeSource:
    """A GitHubSource that returns a fixed list of repositories."""

    def __init__(self, repos: list[Repository]) -> None:
        self._repos = repos

    def fetch_repositories(self, org: str) -> list[Repository]:
        return self._repos


def _config() -> Config:
    return Config(org="lsst")


def test_archived_and_disabled_excluded_by_default() -> None:
    source = FakeSource(
        [
            Repository(name="live", url="https://x", pushed_at=NOW),
            Repository(name="old", url="https://x", is_archived=True),
            Repository(name="dead", url="https://x", is_disabled=True),
        ]
    )
    summaries = build_summaries(source, _config(), now=NOW)
    names = {s.repo.name for s in summaries}
    assert names == {"live"}


def test_include_archived_flag_reincludes_them() -> None:
    source = FakeSource(
        [
            Repository(name="live", url="https://x", pushed_at=NOW),
            Repository(name="old", url="https://x", is_archived=True),
        ]
    )
    summaries = build_summaries(source, _config(), now=NOW, include_archived=True)
    names = {s.repo.name for s in summaries}
    assert names == {"live", "old"}
    archived = next(s for s in summaries if s.repo.name == "old")
    assert archived.activity is ActivityStatus.ARCHIVED


def test_include_disabled_flag_reincludes_them() -> None:
    source = FakeSource(
        [
            Repository(name="live", url="https://x", pushed_at=NOW),
            Repository(name="dead", url="https://x", is_disabled=True),
        ]
    )
    summaries = build_summaries(source, _config(), now=NOW, include_disabled=True)
    names = {s.repo.name for s in summaries}
    assert names == {"live", "dead"}


def test_summaries_carry_activity_and_group() -> None:
    source = FakeSource([Repository(name="live", url="https://x", pushed_at=NOW)])
    summaries = build_summaries(source, _config(), now=NOW)
    assert summaries[0].activity is ActivityStatus.ACTIVE
    assert summaries[0].activity_at == NOW
    assert summaries[0].group == "Uncategorized"
    assert summaries[0].grouping_reason == "fallback"
