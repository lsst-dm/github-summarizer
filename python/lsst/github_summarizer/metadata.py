"""Plan repository metadata updates from edited CSV reports."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .models import Repository

__all__ = [
    "MetadataChange",
    "MetadataError",
    "MetadataField",
    "MetadataPlan",
    "MetadataRow",
    "load_metadata_csv",
    "parse_metadata_csv",
    "plan_metadata_updates",
]


class MetadataError(Exception):
    """Raised when a metadata CSV cannot be interpreted safely."""


class MetadataField(StrEnum):
    """Editable metadata fields supported by the CSV round trip."""

    DESCRIPTION = "description"
    TOPICS = "topics"


MetadataValue = str | tuple[str, ...]


@dataclass(frozen=True)
class MetadataRow:
    """Editable metadata loaded from a CSV row."""

    name: str
    description: str
    topics: tuple[str, ...]


@dataclass(frozen=True)
class MetadataChange:
    """A pending or skipped metadata change for one repository field."""

    repo: str
    field: MetadataField
    baseline: MetadataValue
    current: MetadataValue
    desired: MetadataValue


@dataclass(frozen=True)
class MetadataPlan:
    """Metadata changes classified by update safety."""

    changes: tuple[MetadataChange, ...]
    conflicts: tuple[MetadataChange, ...]
    already_current: tuple[MetadataChange, ...]
    missing_repositories: tuple[str, ...]

    @property
    def has_work(self) -> bool:
        """Return whether any change could be applied."""
        return bool(self.changes or self.conflicts)


_REQUIRED_COLUMNS = frozenset({"name", "description", "topics"})


def load_metadata_csv(path: Path) -> dict[str, MetadataRow]:
    """Load editable repository metadata from a CSV file.

    Parameters
    ----------
    path : `~pathlib.Path`
        CSV file path.

    Returns
    -------
    rows : `dict` [`str`, `MetadataRow`]
        Rows keyed by repository name.

    Raises
    ------
    MetadataError
        Raised when the file is not a usable metadata CSV.
    """
    try:
        return parse_metadata_csv(path.read_text(), source=str(path))
    except OSError as exc:
        raise MetadataError(f"could not read metadata CSV {path}: {exc}") from exc


def parse_metadata_csv(text: str, *, source: str = "CSV") -> dict[str, MetadataRow]:
    """Parse editable repository metadata from report CSV text.

    Parameters
    ----------
    text : `str`
        CSV content.
    source : `str`, optional
        Human-readable source name used in error messages.

    Returns
    -------
    rows : `dict` [`str`, `MetadataRow`]
        Rows keyed by repository name.

    Raises
    ------
    MetadataError
        Raised when required columns are missing or repository names are
        duplicated.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise MetadataError(f"{source} is empty")

    missing = _REQUIRED_COLUMNS.difference(reader.fieldnames)
    if missing:
        joined = ", ".join(sorted(missing))
        raise MetadataError(f"{source} is missing required column(s): {joined}")

    rows: dict[str, MetadataRow] = {}
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise MetadataError(f"{source} row {row_number} has too many columns")
        name = (row["name"] or "").strip()
        if not name:
            raise MetadataError(f"{source} row {row_number} has no repository name")
        if name in rows:
            raise MetadataError(f"{source} has duplicate repository row: {name}")
        rows[name] = MetadataRow(
            name=name,
            description=_normalize_description(row["description"]),
            topics=_parse_topics(row["topics"]),
        )
    return rows


def plan_metadata_updates(
    baseline_rows: dict[str, MetadataRow],
    edited_rows: dict[str, MetadataRow],
    current_repositories: list[Repository],
) -> MetadataPlan:
    """Compare baseline, edited, and current metadata.

    Only cells changed from the baseline CSV are considered intentional edits.
    A changed cell becomes a conflict when the live GitHub value no longer
    matches the baseline value, which protects against overwriting metadata
    updated after the CSV export.

    Parameters
    ----------
    baseline_rows : `dict` [`str`, `MetadataRow`]
        Metadata rows from the original exported report.
    edited_rows : `dict` [`str`, `MetadataRow`]
        Metadata rows after spreadsheet editing.
    current_repositories : `list` [`Repository`]
        Live repositories fetched from GitHub before applying updates.

    Returns
    -------
    plan : `MetadataPlan`
        Classified update plan.

    Raises
    ------
    MetadataError
        Raised when the edited CSV contains a repository absent from the
        baseline CSV.
    """
    unknown = sorted(set(edited_rows).difference(baseline_rows), key=str.lower)
    if unknown:
        joined = ", ".join(unknown)
        raise MetadataError(f"edited CSV contains repositories not present in baseline: {joined}")

    current_rows = {_repo.name: _row_from_repository(_repo) for _repo in current_repositories}
    changes: list[MetadataChange] = []
    conflicts: list[MetadataChange] = []
    already_current: list[MetadataChange] = []
    missing_repositories: set[str] = set()

    for name in sorted(edited_rows, key=str.lower):
        baseline = baseline_rows[name]
        edited = edited_rows[name]
        current = current_rows.get(name)
        for field in MetadataField:
            baseline_value = _value_for_field(baseline, field)
            desired_value = _value_for_field(edited, field)
            if _metadata_key(field, baseline_value) == _metadata_key(field, desired_value):
                continue
            if current is None:
                missing_repositories.add(name)
                continue

            current_value = _value_for_field(current, field)
            change = MetadataChange(
                repo=name,
                field=field,
                baseline=baseline_value,
                current=current_value,
                desired=desired_value,
            )
            if _metadata_key(field, current_value) == _metadata_key(field, desired_value):
                already_current.append(change)
            elif _metadata_key(field, current_value) != _metadata_key(field, baseline_value):
                conflicts.append(change)
            else:
                changes.append(change)

    return MetadataPlan(
        changes=tuple(changes),
        conflicts=tuple(conflicts),
        already_current=tuple(already_current),
        missing_repositories=tuple(sorted(missing_repositories, key=str.lower)),
    )


def _normalize_description(value: str | None) -> str:
    return (value or "").strip()


def _parse_topics(value: str | None) -> tuple[str, ...]:
    raw = (value or "").strip()
    if not raw:
        return ()

    delimiter = ";" if ";" in raw else ","
    topics: list[str] = []
    seen: set[str] = set()
    for item in raw.split(delimiter):
        topic = item.strip().lower()
        if topic and topic not in seen:
            seen.add(topic)
            topics.append(topic)
    return tuple(topics)


def _row_from_repository(repo: Repository) -> MetadataRow:
    return MetadataRow(
        name=repo.name,
        description=_normalize_description(repo.description),
        topics=tuple(topic.lower() for topic in repo.topics),
    )


def _value_for_field(row: MetadataRow, field: MetadataField) -> MetadataValue:
    if field is MetadataField.DESCRIPTION:
        return row.description
    return row.topics


def _metadata_key(field: MetadataField, value: MetadataValue) -> MetadataValue:
    if field is MetadataField.TOPICS:
        if not isinstance(value, tuple):
            raise TypeError("topic metadata value must be a tuple")
        return tuple(sorted(value))
    return value
