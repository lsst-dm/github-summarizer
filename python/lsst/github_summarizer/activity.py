"""Activity status classification from repository activity timestamps."""

from __future__ import annotations

from datetime import datetime, timedelta

from .config import ActivityConfig
from .models import ActivityStatus, Repository

__all__ = ["activity_timestamp", "classify_activity"]


def classify_activity(repo: Repository, cfg: ActivityConfig, *, now: datetime) -> ActivityStatus:
    """Classify a repository's activity status.

    Disabled and archived states take precedence over date-based
    classification.

    Parameters
    ----------
    repo : `Repository`
        The repository to classify.
    cfg : `ActivityConfig`
        Day thresholds for the date-based classification.
    now : `datetime.datetime`
        The reference time-zone-aware timestamp to measure age against.

    Returns
    -------
    status : `ActivityStatus`
        The computed activity status.
    """
    if repo.is_disabled:
        return ActivityStatus.DISABLED
    if repo.is_archived:
        return ActivityStatus.ARCHIVED
    activity_at = activity_timestamp(repo, cfg)
    if activity_at is None:
        return ActivityStatus.DORMANT

    age_days = (now - activity_at).days
    if age_days <= cfg.active_days:
        return ActivityStatus.ACTIVE
    if age_days <= cfg.warm_days:
        return ActivityStatus.WARM
    if age_days <= cfg.quiet_days:
        return ActivityStatus.QUIET
    return ActivityStatus.DORMANT


def activity_timestamp(repo: Repository, cfg: ActivityConfig) -> datetime | None:
    """Return the timestamp activity classification should use.

    Recent default-branch commit history is preferred over repository
    ``pushed_at`` because a single maintenance push can make otherwise dormant
    repositories appear fresh. When the newest commit is separated from the
    previous commit by more than the configured quiet window, the previous
    commit is used as the meaningful activity timestamp.
    """
    commit_dates = sorted(repo.recent_commit_dates, reverse=True)
    if len(commit_dates) >= 2:
        newest = commit_dates[0]
        previous = commit_dates[1]
        if newest - previous > timedelta(days=cfg.quiet_days):
            return previous
        return newest
    if commit_dates:
        return commit_dates[0]
    return repo.pushed_at
