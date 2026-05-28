"""Render repository summaries to markdown, CSV, and JSON."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from .config import Config
from .models import ActivityStatus, RepositorySummary

__all__ = ["render_csv", "render_json", "render_markdown"]

_CSV_COLUMNS = [
    "name",
    "url",
    "description",
    "primary_language",
    "topics",
    "pushed_at",
    "is_archived",
    "is_disabled",
    "default_branch",
    "activity",
    "group",
    "grouping_reason",
]

_TABLE_HEADER = (
    "| Repo | Description | Language | Topics | Last push | Activity | Archived | Disabled | Reason |"
)
_TABLE_DIVIDER = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def _summary_counts(summaries: list[RepositorySummary]) -> dict[ActivityStatus, int]:
    counts = dict.fromkeys(ActivityStatus, 0)
    for summary in summaries:
        counts[summary.activity] += 1
    return counts


def render_json(summaries: list[RepositorySummary], config: Config, generated_at: datetime) -> str:
    """Render summaries as a JSON document.

    Parameters
    ----------
    summaries : `list` [ `RepositorySummary` ]
        The enriched repositories.
    config : `Config`
        The configuration (used for the org name).
    generated_at : `datetime.datetime`
        Report generation timestamp.

    Returns
    -------
    text : `str`
        The JSON document.
    """
    counts = _summary_counts(summaries)
    payload = {
        "generated_at": generated_at.isoformat(),
        "org": config.org,
        "summary": {
            "total": len(summaries),
            **{status.value: counts[status] for status in ActivityStatus},
        },
        "repositories": [summary.model_dump(mode="json") for summary in summaries],
    }
    return json.dumps(payload, indent=2)


def render_csv(summaries: list[RepositorySummary], config: Config, generated_at: datetime) -> str:
    """Render summaries as CSV, one row per repository.

    Parameters
    ----------
    summaries : `list` [ `RepositorySummary` ]
        The enriched repositories.
    config : `Config`
        Unused; present for a uniform writer signature.
    generated_at : `datetime.datetime`
        Unused; present for a uniform writer signature.

    Returns
    -------
    text : `str`
        The CSV document.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_COLUMNS)
    for summary in summaries:
        repo = summary.repo
        writer.writerow(
            [
                repo.name,
                repo.url,
                repo.description or "",
                repo.primary_language or "",
                ";".join(repo.topics),
                repo.pushed_at.isoformat() if repo.pushed_at else "",
                repo.is_archived,
                repo.is_disabled,
                repo.default_branch or "",
                summary.activity.value,
                summary.group,
                summary.grouping_reason,
            ]
        )
    return output.getvalue()


def render_markdown(
    summaries: list[RepositorySummary],
    config: Config,
    generated_at: datetime,
    *,
    include_appendix: bool = False,
) -> str:
    """Render summaries as a grouped markdown report.

    Parameters
    ----------
    summaries : `list` [ `RepositorySummary` ]
        The enriched repositories.
    config : `Config`
        Configuration providing group descriptions and the fallback group.
    generated_at : `datetime.datetime`
        Report generation timestamp.
    include_appendix : `bool`, optional
        Append a full raw-inventory table. Defaults to `False`.

    Returns
    -------
    text : `str`
        The markdown document.
    """
    descriptions = {rule.name: rule.description for rule in config.groups}
    by_group: dict[str, list[RepositorySummary]] = {}
    for summary in summaries:
        by_group.setdefault(summary.group, []).append(summary)

    lines: list[str] = [
        f"# GitHub Repository Report: {config.org}",
        "",
        f"_Generated {generated_at.isoformat()}_",
        "",
        "## Summary",
        "",
        f"- Total repositories: {len(summaries)}",
    ]
    counts = _summary_counts(summaries)
    for status in ActivityStatus:
        lines.append(f"- {status.value.capitalize()}: {counts[status]}")
    lines.append("")

    fallback = config.fallback_group
    ordered = sorted(name for name in by_group if name != fallback)
    for name in ordered:
        _render_group(lines, name, by_group[name], descriptions.get(name))
    if fallback in by_group:
        _render_group(lines, fallback, by_group[fallback], descriptions.get(fallback))

    if include_appendix:
        lines.append("## Appendix: Full inventory")
        lines.append("")
        _render_table(lines, summaries)

    return "\n".join(lines) + "\n"


def _render_group(
    lines: list[str],
    name: str,
    summaries: list[RepositorySummary],
    description: str | None,
) -> None:
    lines.append(f"## {name}")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")
    lines.append(f"{len(summaries)} repositories.")
    lines.append("")
    _render_table(lines, summaries)


def _render_table(lines: list[str], summaries: list[RepositorySummary]) -> None:
    lines.append(_TABLE_HEADER)
    lines.append(_TABLE_DIVIDER)
    for summary in sorted(summaries, key=lambda s: s.repo.name):
        repo = summary.repo
        last_push = repo.pushed_at.date().isoformat() if repo.pushed_at else ""
        lines.append(
            f"| [{repo.name}]({repo.url}) "
            f"| {repo.description or ''} "
            f"| {repo.primary_language or ''} "
            f"| {', '.join(repo.topics)} "
            f"| {last_push} "
            f"| {summary.activity.value} "
            f"| {'yes' if repo.is_archived else 'no'} "
            f"| {'yes' if repo.is_disabled else 'no'} "
            f"| {summary.grouping_reason} |"
        )
    lines.append("")
