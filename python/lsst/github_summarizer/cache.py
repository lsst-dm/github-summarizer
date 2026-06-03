"""Save and reload raw repository data for offline report iteration."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .models import Repository

__all__ = [
    "CacheError",
    "RawFileSource",
    "RawRepositories",
    "load_raw",
    "save_raw",
]

_LOG = logging.getLogger(__name__)
_SCHEMA_VERSION = 1


class CacheError(Exception):
    """Raised when a raw cache file cannot be written, read, or validated."""


class RawRepositories(BaseModel):
    """Envelope of raw repository data saved to disk."""

    schema_version: int = _SCHEMA_VERSION
    org: str
    fetched_at: datetime
    repositories: list[Repository]


def save_raw(path: Path, repositories: list[Repository], *, org: str, fetched_at: datetime) -> None:
    """Write repositories to a `RawRepositories` envelope at ``path``.

    Parameters
    ----------
    path : `pathlib.Path`
        Destination file.
    repositories : `list` [ `Repository` ]
        Raw repositories to save.
    org : `str`
        Organization the data came from.
    fetched_at : `datetime.datetime`
        When the data was fetched.

    Raises
    ------
    CacheError
        If the file cannot be written.
    """
    envelope = RawRepositories(org=org, fetched_at=fetched_at, repositories=repositories)
    try:
        path.write_text(json.dumps(envelope.model_dump(mode="json"), indent=2))
    except OSError as exc:
        raise CacheError(f"cannot write cache {path}: {exc}") from exc


def load_raw(path: Path) -> RawRepositories:
    """Read and validate a raw cache file.

    Parameters
    ----------
    path : `pathlib.Path`
        Cache file to read.

    Returns
    -------
    data : `RawRepositories`
        The validated envelope.

    Raises
    ------
    CacheError
        If the file is missing, not valid JSON, carries an unknown
        ``schema_version``, or fails schema validation.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise CacheError(f"cannot read cache {path}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CacheError(f"invalid JSON in cache {path}: {exc}") from exc

    if isinstance(data, dict) and data.get("schema_version") != _SCHEMA_VERSION:
        raise CacheError(
            f"unsupported cache schema_version {data.get('schema_version')!r} "
            f"in {path} (expected {_SCHEMA_VERSION})"
        )

    try:
        return RawRepositories.model_validate(data)
    except ValidationError as exc:
        raise CacheError(f"invalid cache {path}: {exc}") from exc


class RawFileSource:
    """A `GitHubSource` backed by a loaded `RawRepositories` envelope."""

    def __init__(self, data: RawRepositories) -> None:
        self._data = data

    def fetch_repositories(self, org: str) -> list[Repository]:
        """Return the cached repositories.

        Parameters
        ----------
        org : `str`
            The organization the caller expects; only used to warn on a
            mismatch with the cached organization.

        Returns
        -------
        repos : `list` [ `Repository` ]
            The cached repositories.
        """
        if org != self._data.org:
            _LOG.warning(
                "Requested org %r but cache holds org %r; using cached data",
                org,
                self._data.org,
            )
        return self._data.repositories
