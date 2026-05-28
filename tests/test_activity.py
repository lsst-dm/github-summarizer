"""Tests for activity classification."""

from datetime import UTC, datetime, timedelta

from lsst.github_summarizer.activity import classify_activity
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
