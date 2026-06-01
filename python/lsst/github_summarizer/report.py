"""Render repository summaries to markdown, CSV, JSON, and Typst."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from .config import Config
from .models import ActivityStatus, RepositorySummary

__all__ = ["render_csv", "render_json", "render_markdown", "render_typst"]

_CSV_COLUMNS = [
    "name",
    "url",
    "description",
    "primary_language",
    "topics",
    "pushed_at",
    "activity_at",
    "is_archived",
    "is_disabled",
    "default_branch",
    "activity",
    "group",
    "grouping_reason",
]

_BASE_TABLE_HEADERS = ("Repo", "Description", "Language", "Topics", "Last push", "Activity date", "Activity")
_TYPST_TABLE_COLUMN_WIDTHS = {
    "Repo": "1.15in",
    "Description": "2.4fr",
    "Language": "0.70in",
    "Topics": "0.8fr",
    "Last push": "0.62in",
    "Activity date": "0.68in",
    "Activity": "0.58in",
    "Archived": "0.55in",
    "Disabled": "0.55in",
    "Reason": "0.85in",
}
_TYPST_TABLE_COLUMN_ALIGNS = {
    "Archived": "center",
    "Disabled": "center",
}


def _summary_counts(summaries: list[RepositorySummary]) -> dict[ActivityStatus, int]:
    counts: dict[ActivityStatus, int] = dict.fromkeys(ActivityStatus, 0)
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
                summary.activity_at.isoformat() if summary.activity_at else "",
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
    fetched_at: datetime | None = None,
    include_archived: bool = False,
    include_disabled: bool = False,
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
    fetched_at : `datetime.datetime` or `None`, optional
        When the underlying data was fetched from GitHub. When provided (e.g.
        rendering from a cache), a note is added so cache staleness is
        visible. Defaults to `None`, which omits the note.
    include_archived : `bool`, optional
        Whether archived repositories were included. Controls whether the
        archived count appears in the summary; when `False` the count is
        omitted rather than reported as zero (which would misleadingly
        suggest there are none). Defaults to `False`.
    include_disabled : `bool`, optional
        Whether disabled repositories were included, controlling the disabled
        summary count in the same way as ``include_archived``. Defaults to
        `False`.
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

    lines: list[str] = [f"# GitHub Repository Report: {config.org}", ""]
    lines.append(f"_Generated {generated_at.isoformat()}_")
    if fetched_at is not None:
        lines.append(f"_Data fetched {fetched_at.isoformat()}_")
    lines += ["", "## Summary", "", f"- Total repositories: {len(summaries)}"]
    counts = _summary_counts(summaries)
    # Date-based statuses are always reported. Archived/disabled are only
    # shown when included; otherwise a zero would imply there are none when
    # they were simply filtered out.
    reported = [
        ActivityStatus.ACTIVE,
        ActivityStatus.WARM,
        ActivityStatus.QUIET,
        ActivityStatus.DORMANT,
        ActivityStatus.ABANDONED,
    ]
    if include_archived:
        reported.append(ActivityStatus.ARCHIVED)
    if include_disabled:
        reported.append(ActivityStatus.DISABLED)
    for status in reported:
        lines.append(f"- {status.value.capitalize()}: {counts[status]}")
    lines.append("")

    fallback = config.fallback_group
    ordered = sorted(name for name in by_group if name != fallback)
    for name in ordered:
        _render_group(
            lines,
            name,
            by_group[name],
            descriptions.get(name),
            include_archived,
            include_disabled,
        )
    if fallback in by_group:
        _render_group(
            lines,
            fallback,
            by_group[fallback],
            descriptions.get(fallback),
            include_archived,
            include_disabled,
        )

    if include_appendix:
        lines.append("## Appendix: Full inventory")
        lines.append("")
        _render_table(lines, summaries, include_archived, include_disabled)

    return "\n".join(lines) + "\n"


def render_typst(
    summaries: list[RepositorySummary],
    config: Config,
    generated_at: datetime,
    *,
    fetched_at: datetime | None = None,
    include_archived: bool = False,
    include_disabled: bool = False,
    include_appendix: bool = False,
) -> str:
    """Render summaries as a grouped Typst report.

    Parameters
    ----------
    summaries : `list` [ `RepositorySummary` ]
        The enriched repositories.
    config : `Config`
        Configuration providing group descriptions and the fallback group.
    generated_at : `datetime.datetime`
        Report generation timestamp.
    fetched_at : `datetime.datetime` or `None`, optional
        When the underlying data was fetched from GitHub. When provided (e.g.
        rendering from a cache), a note is added so cache staleness is
        visible. Defaults to `None`, which omits the note.
    include_archived : `bool`, optional
        Whether archived repositories were included. Controls whether the
        archived count appears in the summary. Defaults to `False`.
    include_disabled : `bool`, optional
        Whether disabled repositories were included, controlling the disabled
        summary count in the same way as ``include_archived``. Defaults to
        `False`.
    include_appendix : `bool`, optional
        Append a full raw-inventory table. Defaults to `False`.

    Returns
    -------
    text : `str`
        The Typst document.
    """
    descriptions = {rule.name: rule.description for rule in config.groups}
    by_group: dict[str, list[RepositorySummary]] = {}
    for summary in summaries:
        by_group.setdefault(summary.group, []).append(summary)

    lines = [
        '#set page(paper: "us-letter", flipped: true, margin: (x: 0.35in, y: 0.35in))',
        "#set text(size: 8pt)",
        "#set par(justify: false, leading: 0.55em)",
        "#set table(inset: 2pt, stroke: none)",
        "#show heading.where(level: 1): set text(size: 14pt)",
        "#show heading.where(level: 2): set text(size: 10pt)",
        "",
        f"= GitHub Repository Report: {_typst_text(config.org)}",
        "",
        f"#emph[Generated {_typst_text(generated_at.isoformat())}]",
    ]
    if fetched_at is not None:
        lines.append(f"#emph[Data fetched {_typst_text(fetched_at.isoformat())}]")
    lines += ["", "== Summary", ""]
    _render_typst_summary(lines, summaries, include_archived, include_disabled)

    fallback = config.fallback_group
    ordered = sorted(name for name in by_group if name != fallback)
    for name in ordered:
        _render_typst_group(
            lines,
            name,
            by_group[name],
            descriptions.get(name),
            include_archived,
            include_disabled,
        )
    if fallback in by_group:
        _render_typst_group(
            lines,
            fallback,
            by_group[fallback],
            descriptions.get(fallback),
            include_archived,
            include_disabled,
        )

    if include_appendix:
        lines.append("== Appendix: Full inventory")
        lines.append("")
        _render_typst_table(lines, summaries, include_archived, include_disabled)

    return "\n".join(lines) + "\n"


def _render_group(
    lines: list[str],
    name: str,
    summaries: list[RepositorySummary],
    description: str | None,
    include_archived: bool,
    include_disabled: bool,
) -> None:
    lines.append(f"## {name}")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")
    lines.append(f"{len(summaries)} repositories.")
    lines.append("")
    _render_table(lines, summaries, include_archived, include_disabled)


def _render_table(
    lines: list[str],
    summaries: list[RepositorySummary],
    include_archived: bool,
    include_disabled: bool,
) -> None:
    headers = _table_headers(include_archived, include_disabled)
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for summary in sorted(summaries, key=lambda s: s.repo.name.lower()):
        lines.append(
            "| " + " | ".join(_markdown_table_cells(summary, include_archived, include_disabled)) + " |"
        )
    lines.append("")


def _render_typst_summary(
    lines: list[str],
    summaries: list[RepositorySummary],
    include_archived: bool,
    include_disabled: bool,
) -> None:
    lines.append(f"- Total repositories: {len(summaries)}")
    counts = _summary_counts(summaries)
    reported = [
        ActivityStatus.ACTIVE,
        ActivityStatus.WARM,
        ActivityStatus.QUIET,
        ActivityStatus.DORMANT,
        ActivityStatus.ABANDONED,
    ]
    if include_archived:
        reported.append(ActivityStatus.ARCHIVED)
    if include_disabled:
        reported.append(ActivityStatus.DISABLED)
    for status in reported:
        lines.append(f"- {status.value.capitalize()}: {counts[status]}")
    lines.append("")


def _render_typst_group(
    lines: list[str],
    name: str,
    summaries: list[RepositorySummary],
    description: str | None,
    include_archived: bool,
    include_disabled: bool,
) -> None:
    lines.append(f"== {_typst_text(name)}")
    lines.append("")
    if description:
        lines.append(_typst_text(description))
        lines.append("")
    lines.append(f"{len(summaries)} repositories.")
    lines.append("")
    _render_typst_table(lines, summaries, include_archived, include_disabled)


def _render_typst_table(
    lines: list[str],
    summaries: list[RepositorySummary],
    include_archived: bool,
    include_disabled: bool,
) -> None:
    headers = _table_headers(include_archived, include_disabled)
    column_widths = ", ".join(_TYPST_TABLE_COLUMN_WIDTHS[header] for header in headers)
    column_aligns = ", ".join(_TYPST_TABLE_COLUMN_ALIGNS.get(header, "left") for header in headers)
    lines += [
        "#table(",
        f"  columns: ({column_widths}),",
        f"  align: ({column_aligns}),",
        "  table.header(",
        "    " + ", ".join(f"[{header}]" for header in headers) + ",",
        "  ),",
        "  table.hline(),",
    ]
    for summary in sorted(summaries, key=lambda s: s.repo.name.lower()):
        cells = _typst_table_cells(summary, include_archived, include_disabled)
        lines.append("  " + ", ".join(cells) + ",")
    lines.append(")")
    lines.append("")


def _table_headers(include_archived: bool, include_disabled: bool) -> tuple[str, ...]:
    """Return report table columns for the included repository states."""
    headers = list(_BASE_TABLE_HEADERS)
    if include_archived:
        headers.append("Archived")
    if include_disabled:
        headers.append("Disabled")
    headers.append("Reason")
    return tuple(headers)


def _table_values(
    summary: RepositorySummary,
    include_archived: bool,
    include_disabled: bool,
) -> dict[str, str]:
    """Return unescaped display values for one table row."""
    repo = summary.repo
    values = {
        "Repo": repo.name,
        "Description": repo.description or "",
        "Language": repo.primary_language or "",
        "Topics": ", ".join(repo.topics),
        "Last push": repo.pushed_at.date().isoformat() if repo.pushed_at else "",
        "Activity date": summary.activity_at.date().isoformat() if summary.activity_at else "",
        "Activity": summary.activity.value,
        "Reason": summary.grouping_reason,
    }
    if include_archived:
        values["Archived"] = "yes" if repo.is_archived else "no"
    if include_disabled:
        values["Disabled"] = "yes" if repo.is_disabled else "no"
    return values


def _markdown_table_cells(
    summary: RepositorySummary,
    include_archived: bool,
    include_disabled: bool,
) -> list[str]:
    """Return Markdown table cells for one summary."""
    values = _table_values(summary, include_archived, include_disabled)
    values["Repo"] = f"[{summary.repo.name}]({summary.repo.url})"
    return [values[header] for header in _table_headers(include_archived, include_disabled)]


def _typst_table_cells(
    summary: RepositorySummary,
    include_archived: bool,
    include_disabled: bool,
) -> list[str]:
    """Return Typst table cells for one summary."""
    values = _table_values(summary, include_archived, include_disabled)
    values["Repo"] = f"#link({_typst_string(summary.repo.url)})[{_typst_text(summary.repo.name)}]"
    return [
        f"[{_typst_text(values[header])}]" if header != "Repo" else f"[{values[header]}]"
        for header in _table_headers(include_archived, include_disabled)
    ]


def _typst_text(value: str) -> str:
    """Escape a string for Typst markup content."""
    escapes = {
        "\\": "\\\\",
        "[": "\\[",
        "]": "\\]",
        "{": "\\{",
        "}": "\\}",
        "#": "\\#",
        "$": "\\$",
        "_": "\\_",
        "*": "\\*",
        "<": "\\<",
        ">": "\\>",
    }
    return "".join(escapes.get(char, char) for char in value.replace("\n", " "))


def _typst_string(value: str) -> str:
    """Escape a string for a Typst string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'
