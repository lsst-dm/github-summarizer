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


class RepositorySummary(BaseModel):
    """A repository plus the data derived by the pipeline."""

    repo: Repository
    activity: ActivityStatus
    group: str
    grouping_reason: str
