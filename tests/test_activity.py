"""Tests for activity classification."""

from datetime import UTC, datetime, timedelta

from lsst.github_summarizer.activity import activity_timestamp, classify_activity
from lsst.github_summarizer.config import ActivityConfig
from lsst.github_summarizer.models import ActivityStatus, Repository

NOW = datetime(2026, 6, 1, tzinfo=UTC)
CFG = ActivityConfig()


def _repo(**kwargs: object) -> Repository:
    base: dict[str, object] = {"name": "r", "url": "https://r"}
    base.update(kwargs)
    return Repository(**base)  # type: ignore[arg-type]


def test_disabled_takes_precedence_over_recent_push() -> None:
    repo = _repo(is_disabled=True, is_archived=True, pushed_at=NOW)
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.DISABLED


def test_archived_takes_precedence_over_recent_push() -> None:
    repo = _repo(is_archived=True, pushed_at=NOW)
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.ARCHIVED


def test_active_within_threshold() -> None:
    repo = _repo(pushed_at=NOW - timedelta(days=10))
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.ACTIVE


def test_warm_between_active_and_warm_thresholds() -> None:
    repo = _repo(pushed_at=NOW - timedelta(days=120))
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.WARM


def test_quiet_between_warm_and_quiet_thresholds() -> None:
    repo = _repo(pushed_at=NOW - timedelta(days=300))
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.QUIET


def test_dormant_beyond_quiet_threshold() -> None:
    repo = _repo(pushed_at=NOW - timedelta(days=400))
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.DORMANT


def test_no_push_date_is_dormant() -> None:
    repo = _repo(pushed_at=None)
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.DORMANT


def test_recent_commit_history_supplies_activity_timestamp() -> None:
    repo = _repo(
        pushed_at=NOW - timedelta(days=500),
        recent_commit_dates=[NOW - timedelta(days=10), NOW - timedelta(days=20)],
    )
    assert activity_timestamp(repo, CFG) == NOW - timedelta(days=10)
    assert classify_activity(repo, CFG, now=NOW) is ActivityStatus.ACTIVE


def test_isolated_recent_commit_uses_previous_commit_for_activity() -> None:
    now = datetime(2023, 8, 1, tzinfo=UTC)
    workflow_update = datetime(2023, 7, 10, tzinfo=UTC)
    previous_work = datetime(2015, 1, 1, tzinfo=UTC)
    repo = _repo(
        pushed_at=workflow_update,
        recent_commit_dates=[workflow_update, previous_work],
    )
    assert activity_timestamp(repo, CFG) == previous_work
    assert classify_activity(repo, CFG, now=now) is ActivityStatus.DORMANT


def test_nearby_recent_commits_keep_newest_activity_timestamp() -> None:
    now = datetime(2023, 8, 1, tzinfo=UTC)
    newest = datetime(2023, 7, 10, tzinfo=UTC)
    previous = datetime(2023, 6, 15, tzinfo=UTC)
    repo = _repo(pushed_at=newest, recent_commit_dates=[newest, previous])
    assert activity_timestamp(repo, CFG) == newest
    assert classify_activity(repo, CFG, now=now) is ActivityStatus.ACTIVE
