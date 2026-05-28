"""Activity status classification from a repository's last push date."""

from __future__ import annotations

from datetime import datetime

from .config import ActivityConfig
from .models import ActivityStatus, Repository

__all__ = ["classify_activity"]


def classify_activity(repo: Repository, cfg: ActivityConfig, *, now: datetime) -> ActivityStatus:
    """Classify a repository's activity status.

    Disabled and archived states take precedence over the push-date based
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
    if repo.pushed_at is None:
        return ActivityStatus.DORMANT

    age_days = (now - repo.pushed_at).days
    if age_days <= cfg.active_days:
        return ActivityStatus.ACTIVE
    if age_days <= cfg.warm_days:
        return ActivityStatus.WARM
    if age_days <= cfg.quiet_days:
        return ActivityStatus.QUIET
    return ActivityStatus.DORMANT
