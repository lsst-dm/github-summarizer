"""Data models for repositories and their derived summaries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

__all__ = ["ActivityStatus", "Repository", "RepositorySummary"]


class ActivityStatus(StrEnum):
    """Activity classification for a repository."""

    ACTIVE = "active"
    WARM = "warm"
    QUIET = "quiet"
    DORMANT = "dormant"
    ABANDONED = "abandoned"
    ARCHIVED = "archived"
    DISABLED = "disabled"


class Repository(BaseModel):
    """Raw repository record as produced by a source. No derived fields."""

    name: str
    url: str
    description: str | None = None
    primary_language: str | None = None
    topics: list[str] = []
    pushed_at: datetime | None = None
    is_archived: bool = False
    is_disabled: bool = False
    default_branch: str | None = None
    default_branch_commit_count: int | None = None
    recent_commit_dates: list[datetime] = []


class RepositorySummary(BaseModel):
    """A repository plus the data derived by the pipeline."""

    repo: Repository
    activity: ActivityStatus
    activity_at: datetime | None = None
    group: str
    grouping_reason: str
