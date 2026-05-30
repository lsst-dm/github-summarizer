"""Wire the source, filtering, and enrichment stages together."""

from __future__ import annotations

from datetime import datetime

from .activity import activity_timestamp, classify_activity
from .config import Config
from .grouping import Grouper
from .models import RepositorySummary
from .source import GitHubSource

__all__ = ["build_summaries"]


def build_summaries(
    source: GitHubSource,
    config: Config,
    *,
    now: datetime,
    include_archived: bool = False,
    include_disabled: bool = False,
) -> list[RepositorySummary]:
    """Fetch, filter, and enrich repositories into summaries.

    Archived and disabled repositories are dropped unless the corresponding
    flag is set, so excluded repositories leave the report entirely (counts
    included).

    Parameters
    ----------
    source : `GitHubSource`
        The repository source to fetch from.
    config : `Config`
        The validated configuration (its ``org`` drives the fetch).
    now : `datetime.datetime`
        Reference timestamp for activity classification.
    include_archived : `bool`, optional
        Keep archived repositories. Defaults to `False`.
    include_disabled : `bool`, optional
        Keep disabled repositories. Defaults to `False`.

    Returns
    -------
    summaries : `list` [ `RepositorySummary` ]
        Enriched repository summaries.
    """
    repos = source.fetch_repositories(config.org)
    repos = [
        repo
        for repo in repos
        if (include_archived or not repo.is_archived) and (include_disabled or not repo.is_disabled)
    ]

    assignments = Grouper(config).assign(repos)

    summaries: list[RepositorySummary] = []
    for repo in repos:
        group, reason = assignments[repo.name]
        summaries.append(
            RepositorySummary(
                repo=repo,
                activity=classify_activity(repo, config.activity, now=now),
                activity_at=activity_timestamp(repo, config.activity),
                group=group,
                grouping_reason=reason,
            )
        )
    return summaries
